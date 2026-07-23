"""Lint probe — ast-based static checks.

m1 minimal-but-real: parses the span as Python and flags a small, concrete
set of machine-checkable violations (``import *``, bare ``except:``,
``assert`` used for validation, ``print`` left in module body). The m3
milestone will swap this for a real ``ruff``/``pyflakes`` subprocess; the
``run(span) -> ProbeResult`` seam stays identical. A violation fails the
probe — these are exactly the smells that correlate with barreling agents.
"""
from __future__ import annotations

import ast

from ..models import ProbeResult, Span
from .base import Probe


class LintProbe(Probe):
    name = "lint"

    def run(self, span: Span) -> ProbeResult:
        code = span.content
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return ProbeResult(
                probe="lint",
                passed=False,
                evidence=f"cannot lint: SyntaxError line {exc.lineno}: {exc.msg}",
            )

        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                isinstance(n, ast.alias) and n.name == "*" for n in node.names
            ):
                violations.append(f"line {node.lineno}: `from ... import *` forbidden")
            elif isinstance(node, ast.Try):
                for handler in node.handlers:
                    if handler.type is None:
                        violations.append(
                            f"line {handler.lineno}: bare `except:` swallows everything"
                        )
            elif isinstance(node, ast.Assert):
                violations.append(
                    f"line {node.lineno}: `assert` is not validation; use a real check"
                )

        if violations:
            return ProbeResult(probe="lint", passed=False, evidence="; ".join(violations))
        return ProbeResult(probe="lint", passed=True, evidence="no lint violations")
