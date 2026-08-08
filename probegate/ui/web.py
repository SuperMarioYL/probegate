"""ProbeGate local web UI — FastAPI per-span card view.

The web view is the m2 surface, but it ships in m1 so the same
:class:`~probegate.gate.ProbeGate` can be inspected visually. It serves a
static ``gate.html`` (no Jinja2 dep) that calls two JSON endpoints:

* ``GET  /api/demo``  — run the built-in 5-step agent, return spans + decisions.
* ``POST /api/guard`` — run the gate over caller-supplied spans.

v0.3.0:
* ``/api/guard`` now validates the request body against the same constraints as
  :class:`~probegate.models.ProbeGateConfig` (``probe`` Literal, ``uncertainty_threshold``
  ``0..1``, ``model_target`` Literal) so a bad body returns HTTP 422 with a
  usable error instead of an unhandled 500 with a leaked traceback.
* Each ``GateDecision`` is appended to the local audit log
  (:class:`~probegate.audit.LocalAuditLog`, ``.probegate/audit.jsonl`` by
  default) — the deferred m3 stub, scoped LOCAL-only (OSS, NOT the Team paid
  审计日志导出/export/SSO/hosted relay which stays out of scope).

Run with ``probegate ui`` (which calls ``uvicorn`` on this module's ``app``).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..audit import LocalAuditLog
from ..gate import ProbeGate
from ..models import ModelTarget, ProbeGateConfig, ProbeKind, Span

app = FastAPI(
    title="ProbeGate",
    version="0.3.0",
    description="Per-span probe-validation gate for 国产模型 autonomous agents.",
)

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "gate.html"

# v0.3.0: local audit log (OSS observability primitive). Module-level singleton;
# tests + callers can swap it via set_audit_log() to point at a tmp path. The
# default path is .probegate/audit.jsonl relative to the process cwd.
_audit_log = LocalAuditLog()


def set_audit_log(log: LocalAuditLog) -> None:
    """Swap the module-level audit log (test seam / programmatic override)."""
    global _audit_log
    _audit_log = log


def set_default_config(config: ProbeGateConfig | None) -> None:
    """Swap the default gate config used by /api/demo (e.g. from .probegate.toml)."""
    global _default_config
    _default_config = config


_default_config: ProbeGateConfig | None = None


class GuardRequest(BaseModel):
    """A POST body for ``/api/guard`` — mirrors ProbeGateConfig constraints.

    v0.3.0: ``probe``/``uncertainty_threshold``/``model_target`` carry the same
    constraints as :class:`~probegate.models.ProbeGateConfig`, so FastAPI
    rejects a bad body pre-route with 422 instead of letting the gate's
    constructor raise (which surfaced as HTTP 500).
    """

    spans: list[Span]
    uncertainty_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    probe: ProbeKind = "compile"
    model_target: ModelTarget = "deepseek-coder"


@app.get("/")
def index() -> FileResponse:
    """Serve the per-span web view (vanilla JS, no server-side templating)."""
    return FileResponse(_TEMPLATE)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}


@app.get("/api/config")
def get_config() -> dict[str, object]:
    cfg = _default_config or ProbeGateConfig()
    return cfg.model_dump()


@app.get("/api/demo")
def demo() -> dict[str, object]:
    """Run the built-in 5-step demo agent and return spans + decisions."""
    # local import avoids an import-time cycle (cli -> web -> cli)
    from ..cli import build_demo_spans

    spans = build_demo_spans()
    gate = ProbeGate(config=_default_config)
    decisions = [gate.guard(s) for s in spans]
    _audit_log.append_decision_many(decisions)
    return {"spans": [s.model_dump() for s in spans], "decisions": [d.model_dump() for d in decisions]}


@app.post("/api/guard")
def guard(req: GuardRequest) -> dict[str, object]:
    """Run the gate over caller-supplied spans.

    v0.3.0: a bad ``probe`` name or out-of-range ``uncertainty_threshold`` is
    rejected by FastAPI pre-route (422) because ``GuardRequest`` mirrors
    ``ProbeGateConfig`` constraints — it never reaches the gate constructor.
    """
    gate = ProbeGate(
        config=ProbeGateConfig(
            uncertainty_threshold=req.uncertainty_threshold,
            probe=req.probe,
            model_target=req.model_target,
        )
    )
    decisions = [gate.guard(s) for s in req.spans]
    _audit_log.append_decision_many(decisions)
    return {"decisions": [d.model_dump() for d in decisions]}


@app.post("/api/approve")
def approve(span_id: str) -> JSONResponse:
    """Record an operator approval of a handoff span (persists the local audit log)."""
    # v0.3.0: the deferred m3 audit-log stub — persist the operator action to
    # the local jsonl (OSS observability, NOT the Team paid export).
    _audit_log.append_action(span_id=span_id, action="approved", rule="proceed")
    return JSONResponse({"span_id": span_id, "action": "approved", "rule": "proceed"})


@app.post("/api/reject")
def reject(span_id: str) -> JSONResponse:
    """Record an operator rejection — the agent should rewind this span."""
    _audit_log.append_action(span_id=span_id, action="rejected", rule="rewind")
    return JSONResponse({"span_id": span_id, "action": "rejected", "rule": "rewind"})
