"""Test probe — execs ``test_*`` functions embedded in the span.

m1 minimal-but-real: parses the span as Python, finds every top-level
``test_*`` function, execs the module body in a fresh namespace, and runs
each test. Any ``AssertionError`` / raised exception fails the probe with
the failing test name. No test functions => passes (nothing to check). The
m3 milestone will swap this for a real ``pytest`` subprocess run; the seam
(``run(span) -> ProbeResult``) stays identical.
"""
from __future__ import annotations

import ast
import traceback

from ..models import ProbeResult, Span
from .base import Probe


class TestProbe(Probe):
    name = "test"

    def run(self, span: Span) -> ProbeResult:
        code = span.content
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ProbeResult(
                probe="test",
                passed=False,
                evidence=f"cannot parse span to find tests: SyntaxError line {exc.lineno}: {exc.msg}",
            )

        test_names = [
            n.name
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
        ]
        if not test_names:
            return ProbeResult(
                probe="test",
                passed=True,
                evidence="no test_* functions in span — nothing to verify",
            )

        ns: dict[str, object] = {}
        try:
            exec(compile(tree, "<probegate:test>", "exec"), ns)  # noqa: S102 — probe must exec to run tests
        except Exception as exc:  # pragma: no cover — module body failures
            return ProbeResult(
                probe="test",
                passed=False,
                evidence=f"module body raised {type(exc).__name__}: {exc}",
            )

        failures: list[str] = []
        for name in test_names:
            fn = ns.get(name)
            if not callable(fn):
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — any failure is a probe failure
                failures.append(f"{name}: {type(exc).__name__} — {exc}")
                # keep the traceback tail for evidence
                _ = traceback.format_exc().splitlines()[-1]

        if failures:
            return ProbeResult(
                probe="test",
                passed=False,
                evidence="; ".join(failures),
            )
        return ProbeResult(
            probe="test",
            passed=True,
            evidence=f"{len(test_names)} test_* function(s) passed",
        )
