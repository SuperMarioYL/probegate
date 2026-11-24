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
        #
        # v0.10.0 (fix-test-probe-non-def-crash): check the node type FIRST.
        # ``tree.body`` holds every top-level statement, and only def nodes
        # carry ``.name``; the v0.9.0 async refactor dereferenced ``.name``
        # before the isinstance check, so any module-level import, assignment,
        # ``if`` or docstring raised AttributeError out of ``run()`` — past
        # ``ProbeGate.guard`` (which does not wrap the probe call) and past
        # ``cli.main`` (which does not catch AttributeError), tearing down the
        # agent loop the gate exists to protect.
        sync_names: list[str] = []
        async_names: list[str] = []
        for n in tree.body:
            if isinstance(n, ast.AsyncFunctionDef):
                if n.name.startswith("test_"):
                    async_names.append(n.name)
            elif isinstance(n, ast.FunctionDef):
                if n.name.startswith("test_"):
                    sync_names.append(n.name)
        if not sync_names and not async_names:
            return ProbeResult(
                probe="test",
                passed=True,
                evidence="no test_* functions in span — nothing to verify",
                applicable=False,
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
                    # v0.10.0 (fix-inapplicable-probe-fail-open): with no sync
                    # test executed either, this pass verified nothing — the
                    # gate must not read it as a cleared probe signal. If a
                    # sync test did run and pass, the span WAS verified (the
                    # evidence still names the async tests that were skipped).
                    applicable=bool(sync_names),
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
