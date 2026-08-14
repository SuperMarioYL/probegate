"""The dual-signal abstention gate.

The :class:`ProbeGate` is the core primitive: for every span an agent emits,
it reads (a) the model's self-reported uncertainty and (b) runs a
machine-checkable probe, then decides:

* ``proceed``  — confident AND probe passes (no signal trips).
* ``abstain``  — exactly one signal trips; flag but do not hand off.
* ``handoff`` — both signals fire (uncertain AND probe fails) → route to human.

Single signal is insufficient by design: trusting model self-knowledge alone
is a black-box-explanation illusion; trusting a probe alone over-triggers.
Only the AND of both is a trustworthy abstention trigger.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterable

import httpx

from .models import GateDecision, ProbeGateConfig, ProbeResult, Span
from .probes.base import Probe
from .probes.compile_probe import CompileProbe
from .probes.lint_probe import LintProbe
from .probes.schema_probe import SchemaProbe
from .probes.test_probe import TestProbe
from .uncertainty import UncertaintyAdapter, UncertaintyConfigError, UncertaintyFetchError

# Probe registry — the gate *calls* the probe layer (Autohand Code), it does
# not reinvent it. Adding a probe = one entry here + one Probe subclass.
PROBE_REGISTRY: dict[str, type[Probe]] = {
    "compile": CompileProbe,
    "test": TestProbe,
    "lint": LintProbe,
    "schema": SchemaProbe,
}


class ProbeGate:
    """The per-span dual-signal abstention gate.

    Wrap an agent loop with it::

        with ProbeGate() as g:
            for span in agent.run():
                decision = g.guard(span)
                if decision.rule == "handoff":
                    ...  # surface to a human before commit
    """

    def __init__(
        self,
        config: ProbeGateConfig | None = None,
        *,
        uncertainty_threshold: float = 0.5,
        probe: str = "compile",
        model_target: str = "deepseek-coder",
        uncertainty_adapter: UncertaintyAdapter | None = None,
    ) -> None:
        if config is None:
            self.config = ProbeGateConfig(
                uncertainty_threshold=uncertainty_threshold,
                probe=probe,  # type: ignore[arg-type]
                model_target=model_target,
            )
        else:
            self.config = config

        probe_cls = PROBE_REGISTRY.get(self.config.probe)
        if probe_cls is None:
            raise ValueError(
                f"unknown probe '{self.config.probe}'. "
                f"known: {sorted(PROBE_REGISTRY)}"
            )
        self.probe: Probe = probe_cls()
        self.adapter = uncertainty_adapter or UncertaintyAdapter(self.config)
        self.history: list[GateDecision] = []

    # -- public API -------------------------------------------------------

    def guard(self, span: Span) -> GateDecision:
        """Evaluate a single span through the dual-signal gate.

        v0.5.0 (fix-guard-fetch-robustness): the fetch path is now robust — a
        transient 国产模型 API hiccup or a call from inside a running event loop
        degrades to the non-fatal ``read(span)`` path instead of crashing the
        gate (and the agent loop it wraps). For an async caller, prefer
        :meth:`guard_async`, which awaits the fetch directly without
        ``asyncio.run``.
        """
        # (a) uncertainty — m1 reads span.uncertainty directly; m3 fetches
        #     logprobs from the 国产模型 API via the adapter when creds are
        #     set (v0.4.0 fix-guard-never-fetches-logprob: --api-key/
        #     --base-url now actually drive a fetch), falling back to the
        #     m1 read(span) path on UncertaintyConfigError.
        uncertainty = self._resolve_uncertainty(span)
        # normalize back into the span so downstream sees the resolved value
        span = span.model_copy(update={"uncertainty": uncertainty})
        # (b) machine-checkable probe
        probe_result = self.probe.run(span)
        decision = self._evaluate(span, probe_result)
        self.history.append(decision)
        return decision

    async def guard_async(self, span: Span) -> GateDecision:
        """Async-safe dual-signal gate — the path for async agent loops.

        v0.5.0 (fix-guard-fetch-robustness, folded
        fix-guard-fetch-crashes-async-loop): the sync :meth:`guard` drives the
        fetch via ``asyncio.run()``, which raises ``RuntimeError`` when called
        from within a running event loop — the documented primary use case
        ("agent loop 外面包一层 ProbeGate(...)") for a modern async agent loop.
        ``guard_async`` awaits :meth:`UncertaintyAdapter.fetch_logprob` directly
        (no ``asyncio.run``) so the v0.4.0 headline feature works for async
        callers. On a transient fetch error it degrades to the non-fatal
        ``read(span)`` path, same as :meth:`guard`.
        """
        uncertainty = await self._resolve_uncertainty_async(span)
        span = span.model_copy(update={"uncertainty": uncertainty})
        probe_result = self.probe.run(span)
        decision = self._evaluate(span, probe_result)
        self.history.append(decision)
        return decision

    def _resolve_uncertainty(self, span: Span) -> float:
        """Resolve a span's uncertainty, fetching a real logprob when creds set.

        v0.4.0 (fix-guard-never-fetches-logprob): when ``config.api_key`` and
        ``config.base_url`` are set, drive the real httpx logprob fetch
        (:meth:`UncertaintyAdapter.fetch_logprob`) so ``--api-key`` /
        ``--base-url`` actually trigger a fetch end-to-end (the v0.3.0
        fix-init-config-never-read claim of "reachable end-to-end" was not
        delivered as shipped because guard() only ever called read()). Falls
        back to the m1 :meth:`read` identity path on
        :class:`UncertaintyConfigError` per the existing adapter contract — a
        creds-less caller keeps the m1 behaviour and never crashes.

        v0.5.0 (fix-guard-fetch-robustness): broaden the catch so the gate
        never tears down the agent loop on a transient failure. Two folded
        HIGH bug-hunt findings share this method and ship together:
        (1) fix-guard-fetch-crashes-async-loop — ``asyncio.run()`` raises
        ``RuntimeError`` ("cannot be called from a running event loop") when
        :meth:`guard` is called from within a running asyncio loop (an async
        agent loop). Caught → degrade to ``read(span)``; async callers should
        use :meth:`guard_async`.
        (2) fix-guard-fetch-errors-crash-loop — a transient
        :class:`httpx.HTTPError` (timeout / connect / 5xx) or
        :class:`UncertaintyFetchError` (malformed logprobs) propagating up
        would crash the whole agent loop on a single API hiccup. Caught →
        degrade to ``read(span)`` so the safety-net gate does not become the
        cascade it exists to prevent.
        """
        cfg = self.config
        if cfg.api_key and cfg.base_url:
            # If a running event loop already exists (an async agent loop),
            # asyncio.run() would raise RuntimeError AND leak the un-awaited
            # fetch coroutine. Detect that case up front and degrade to the
            # non-fatal read(span) path without ever constructing the
            # coroutine (async callers should use guard_async). This is the
            # fix-guard-fetch-crashes-async-loop half of the robustness fix.
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass  # no running loop -> the sync asyncio.run path is safe
            else:
                return self.adapter.read(span)
            try:
                return asyncio.run(self.adapter.fetch_logprob(span))
            except httpx.HTTPError:
                # Transient serving-tier blip (timeout / connect / 5xx from
                # raise_for_status) -> degrade to the un-trusted-but-non-fatal
                # read(span) so a single API hiccup does not tear down the
                # agent loop (fix-guard-fetch-errors-crash-loop).
                return self.adapter.read(span)
            except (UncertaintyConfigError, UncertaintyFetchError, RuntimeError):
                # UncertaintyConfigError: creds missing at fetch time -> m1 path.
                # UncertaintyFetchError: malformed logprob response -> degrade.
                # RuntimeError: defensive backstop if asyncio.run still raises
                # for any other reason -> degrade, not crash.
                return self.adapter.read(span)
        return self.adapter.read(span)

    async def _resolve_uncertainty_async(self, span: Span) -> float:
        """Async variant of :meth:`_resolve_uncertainty`.

        Awaits :meth:`UncertaintyAdapter.fetch_logprob` directly (no
        ``asyncio.run``), so it is safe inside a running event loop. Degrades
        to the m1 :meth:`read` path on the same exception classes as the sync
        variant.
        """
        cfg = self.config
        if cfg.api_key and cfg.base_url:
            try:
                return await self.adapter.fetch_logprob(span)
            except httpx.HTTPError:
                return self.adapter.read(span)
            except (UncertaintyConfigError, UncertaintyFetchError, RuntimeError):
                return self.adapter.read(span)
        return self.adapter.read(span)

    def guard_many(self, spans: Iterable[Span]) -> list[GateDecision]:
        """Evaluate a stream of spans; returns one decision per span."""
        return [self.guard(s) for s in spans]

    # -- the AND gate -----------------------------------------------------

    def _evaluate(self, span: Span, probe_result: ProbeResult) -> GateDecision:
        tau = self.config.uncertainty_threshold
        high_uncertainty = span.uncertainty > tau
        probe_failed = not probe_result.passed

        signals: list[str] = []
        if high_uncertainty:
            signals.append(
                f"uncertainty {span.uncertainty:.2f} > tau {tau:.2f}"
            )
        if probe_failed:
            signals.append(
                f"probe '{probe_result.probe}' FAILED: {probe_result.evidence or 'no evidence'}"
            )

        if high_uncertainty and probe_failed:
            rule = "handoff"
            rationale = "DUAL SIGNAL: " + " AND ".join(signals) + " => route to human"
        elif high_uncertainty or probe_failed:
            rule = "abstain"
            rationale = "single signal only: " + "; ".join(signals) + " — no handoff (AND gate not satisfied)"
        else:
            rule = "proceed"
            rationale = (
                f"uncertainty {span.uncertainty:.2f} <= tau {tau:.2f} AND "
                f"probe '{probe_result.probe}' passed"
            )

        return GateDecision(
            span_id=span.id,
            rule=rule,
            rationale=rationale,
            probe=probe_result,
            uncertainty=span.uncertainty,
        )

    # -- context manager so `with ProbeGate(...) as g:` reads naturally ----

    def __enter__(self) -> "ProbeGate":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        # m1: nothing to flush; m2/m3 will flush the audit log + web session
        return None
