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

from collections.abc import Iterable

from .models import GateDecision, ProbeGateConfig, ProbeResult, Span
from .probes.base import Probe
from .probes.compile_probe import CompileProbe
from .probes.lint_probe import LintProbe
from .probes.schema_probe import SchemaProbe
from .probes.test_probe import TestProbe
from .uncertainty import UncertaintyAdapter

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
        """Evaluate a single span through the dual-signal gate."""
        # (a) uncertainty — m1 reads span.uncertainty directly; m3 fetches
        #     logprobs from the 国产模型 API via the adapter.
        uncertainty = self.adapter.read(span)
        # normalize back into the span so downstream sees the resolved value
        span = span.model_copy(update={"uncertainty": uncertainty})
        # (b) machine-checkable probe
        probe_result = self.probe.run(span)
        decision = self._evaluate(span, probe_result)
        self.history.append(decision)
        return decision

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
