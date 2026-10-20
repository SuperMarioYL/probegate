"""Test probe — execs ``test_*`` functions embedded in the span.

m1 minimal-but-real: parses the span as Python, finds every top-level
``test_*`` function (sync and async), execs the module body in a fresh
namespace, and runs each test. Any ``AssertionError`` / raised exception fails
the probe with the failing test name. No test functions => passes (nothing to
check). The m3 milestone will swap this for a real ``pytest`` subprocess run;
the seam (``run(span) -> ProbeResult``) stays identical.

v0.9.0 (fix-test-probe-async-tests-skip): ``async def test_*`` functions used
to be invisible to the collector (ast.FunctionDef only), so an async-test span
passed vacuously with the misleading evidence "no test_* functions in span —
nothing to verify" while an actually-failing async test existed. Async tests
are now collected and executed via ``asyncio.run`` on the normal sync path;
when the probe itself runs inside a running event loop (e.g. via
``guard_async``), executing them there would crash (asyncio.run raises
RuntimeError in a running loop), so they are skipped with honest evidence
naming the not-executed tests instead of a false "nothing to verify" pass.
"""
from __future__ import annotations

import ast
import asyncio
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

        # v0.9.0 (fix-test-probe-async-tests-skip): collect BOTH def flavors —
        # an async def test_* is a test_* function and its failures must fail
        # the probe, not vanish behind "no test_* functions" evidence.
        sync_names: list[str] = []
        async_names: list[str] = []
        for n in tree.body:
            if not n.name.startswith("test_"):
                continue
            if isinstance(n, ast.AsyncFunctionDef):
                async_names.append(n.name)
            elif isinstance(n, ast.FunctionDef):
                sync_names.append(n.name)
        if not sync_names and not async_names:
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
        for name in sync_names:
            fn = ns.get(name)
            if not callable(fn):
                continue
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — any failure is a probe failure
                failures.append(f"{name}: {type(exc).__name__} — {exc}")
                # keep the traceback tail for evidence
                _ = traceback.format_exc().splitlines()[-1]

        if async_names:
            # asyncio.run raises RuntimeError inside a running event loop —
            # e.g. the probe reached from guard_async — so detect that context
            # up front and skip with honest evidence instead of crashing the
            # gate or silently mis-reporting.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass  # no running loop -> asyncio.run is safe
            else:
                skipped = ", ".join(async_names)
                if failures:
                    return ProbeResult(
                        probe="test",
                        passed=False,
                        evidence=(
                            "; ".join(failures)
                            + f"; {len(async_names)} async test_* function(s) not executed "
                            "(probe called inside a running event loop)"
                        ),
                    )
                return ProbeResult(
                    probe="test",
                    passed=True,
                    evidence=(
                        f"{len(async_names)} async test_* function(s) not executed "
                        f"({skipped}) — probe called inside a running event loop"
                    ),
                )
            for name in async_names:
                fn = ns.get(name)
                if not callable(fn):
                    continue
                try:
                    asyncio.run(fn())
                except Exception as exc:  # noqa: BLE001 — any failure is a probe failure
                    failures.append(f"{name}: {type(exc).__name__} — {exc}")
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
            evidence=f"{len(sync_names) + len(async_names)} test_* function(s) passed",
        )
