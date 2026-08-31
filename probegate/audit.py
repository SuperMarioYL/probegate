"""Local audit log — append-only jsonl of gate decisions and operator actions.

v0.3.0 completes the web audit-log stub the v0.2.0 grill deferred to "v0.3 or a
Team-extension track". This is a LOCAL observability primitive only: each
:class:`~probegate.models.GateDecision` (span_id, rule, rationale, probe result,
uncertainty) is appended as one JSON line to ``.probegate/audit.jsonl`` (path
overridable). Operator approve/reject actions are appended the same way.

This is explicitly NOT the Team paid ``审计日志导出`` / export / SSO / hosted
relay — that stays out of scope (see ``mvp_plan`` §6). The local log has no
network surface, no auth, no export API; it is a plain append-only file the
developer can ``tail -f`` or ``cat``.
"""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from .models import GateDecision

DEFAULT_AUDIT_PATH = Path(".probegate") / "audit.jsonl"


class LocalAuditLog:
    """Append-only local jsonl audit log of gate decisions + operator actions.

    Thread-safe (a process-local lock serialises writes). Each record is one
    JSON object per line. Records carry a ``type`` discriminator so a reader
    can filter decisions vs. operator actions.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path: Path = Path(path) if path else DEFAULT_AUDIT_PATH
        self._lock = Lock()

    def append_decision(self, decision: GateDecision) -> None:
        """Append one ``GateDecision`` record to the audit log."""
        record: dict[str, Any] = {"type": "decision", **decision.model_dump()}
        self._write(record)

    def append_decision_many(self, decisions: list[GateDecision]) -> None:
        """Append several ``GateDecision`` records (one line each)."""
        for d in decisions:
            self.append_decision(d)

    def append_action(self, span_id: str, action: str, rule: str) -> None:
        """Append an operator action (approve/reject) on a span."""
        record: dict[str, Any] = {
            "type": "action",
            "span_id": span_id,
            "action": action,
            "rule": rule,
        }
        self._write(record)

    def read(self) -> list[dict[str, Any]]:
        """Read every record back (test helper; not on any hot path).

        v0.7.0 (fix-audit-log-read-corrupt-line-crash): the audit log is
        append-only, so a process killed mid-write (or an OS-level partial
        write of a large record, or a hand-edited file) can leave a
        truncated/corrupt last line. A bare ``json.loads`` on such a line
        raised ``json.JSONDecodeError`` and tore down the read, hiding the
        salvageable records exactly when the audit trail is most needed
        (debugging a cascade the gate flagged). A corrupt line is now skipped
        so ``read()`` degrades to the salvageable records instead of raising
        — the same degrade-not-crash posture the v0.5.0/v0.6.0 fetch fixes
        established for the gate itself.
        """
        if not self.path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # a truncated/corrupt line (e.g. a process killed mid-write
                    # left a partial last line) is skipped so the salvageable
                    # records are still returned rather than crashing the read.
                    continue
        return out

    def _write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
