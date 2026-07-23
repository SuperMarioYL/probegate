"""Compile probe — the m1 workhorse.

Treats ``span.content`` as Python source and byte-compiles it. Any
``SyntaxError`` (the deliberately wrong function signature in the demo, a
hallucinated API call that isn't even syntactically valid, etc.) fails the
probe with the offending line. This is the cheapest machine-checkable
verifier and the one the m1 happy path wires by default.
"""
from __future__ import annotations

from ..models import ProbeResult, Span
from .base import Probe


class CompileProbe(Probe):
    name = "compile"

    def run(self, span: Span) -> ProbeResult:
        code = span.content
        try:
            compile(code, "<probegate:span>", "exec")
        except SyntaxError as exc:
            loc = f"line {exc.lineno}" if exc.lineno else "unknown line"
            msg = exc.msg or "syntax error"
            evidence = f"SyntaxError at {loc}: {msg}"
            if exc.text:
                evidence += f" — {exc.text.strip()}"
            return ProbeResult(probe="compile", passed=False, evidence=evidence)
        except Exception as exc:  # pragma: no cover — defensive
            return ProbeResult(
                probe="compile",
                passed=False,
                evidence=f"{type(exc).__name__}: {exc}",
            )
        return ProbeResult(probe="compile", passed=True, evidence="byte-compiled ok")
