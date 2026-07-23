"""Tests for the ProbeGate dual-signal AND gate and the compile probe.

The load-bearing invariant under test::

    handoff <=> uncertainty > tau AND not probe.passed
"""
from __future__ import annotations

import pytest

from probegate.gate import ProbeGate, PROBE_REGISTRY
from probegate.models import GateDecision, ProbeGateConfig, Span


# --------------------------------------------------------------------------
# Span helpers
# --------------------------------------------------------------------------

GOOD_CODE = "def add(a, b):\n    return a + b\n"
BAD_CODE = "def add(a, b:\n    return a + b\n"  # broken signature — missing )


def span(content: str, uncertainty: float, *, id: str = "s", step: int = 1) -> Span:
    return Span(id=id, agent_step=step, content=content, uncertainty=uncertainty)


# --------------------------------------------------------------------------
# Compile probe
# --------------------------------------------------------------------------

class TestCompileProbe:
    def test_valid_code_passes(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        decision = gate.guard(span(GOOD_CODE, 0.1))
        assert decision.probe is not None
        assert decision.probe.passed is True
        assert decision.probe.probe == "compile"

    def test_syntax_error_fails(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        decision = gate.guard(span(BAD_CODE, 0.9))
        assert decision.probe is not None
        assert decision.probe.passed is False
        assert "SyntaxError" in decision.probe.evidence

    def test_evidence_cites_line(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        decision = gate.guard(span("x = (\n", 0.9))
        assert decision.probe is not None
        assert decision.probe.passed is False
        assert "line 1" in decision.probe.evidence


# --------------------------------------------------------------------------
# The AND-gate three states
# --------------------------------------------------------------------------

class TestAndGate:
    def test_proceed_when_both_clear(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(GOOD_CODE, 0.1))
        assert d.rule == "proceed"

    def test_abstain_when_only_uncertainty_trips(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(GOOD_CODE, 0.9))
        assert d.rule == "abstain"

    def test_abstain_when_only_probe_trips(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(BAD_CODE, 0.1))
        assert d.rule == "abstain"

    def test_handoff_when_both_trip(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(BAD_CODE, 0.9))
        assert d.rule == "handoff"

    @pytest.mark.parametrize(
        ("uncertainty", "code", "expected"),
        [
            (0.1, GOOD_CODE, "proceed"),
            (0.9, GOOD_CODE, "abstain"),
            (0.1, BAD_CODE, "abstain"),
            (0.9, BAD_CODE, "handoff"),
        ],
    )
    def test_invariant_matrix(self, uncertainty: float, code: str, expected: str) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(code, uncertainty))
        assert d.rule == expected
        # handoff is exactly the AND of the two signals
        if expected == "handoff":
            assert uncertainty > 0.5
            assert d.probe is not None and d.probe.passed is False
        elif expected == "proceed":
            assert uncertainty <= 0.5
            assert d.probe is not None and d.probe.passed is True


# --------------------------------------------------------------------------
# Threshold + boundary
# --------------------------------------------------------------------------

class TestThreshold:
    def test_default_tau_is_half(self) -> None:
        gate = ProbeGate(probe="compile")
        assert gate.config.uncertainty_threshold == 0.5

    def test_boundary_is_strict_greater_than(self) -> None:
        # uncertainty == tau is NOT high → proceed (with passing probe)
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(span(GOOD_CODE, 0.5))
        assert d.rule == "proceed"

    def test_custom_tau_moves_handoff_boundary(self) -> None:
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.2)
        # 0.3 > 0.2 but probe passes → abstain
        d = gate.guard(span(GOOD_CODE, 0.3))
        assert d.rule == "abstain"
        # 0.3 > 0.2 and probe fails → handoff
        d = gate.guard(span(BAD_CODE, 0.3))
        assert d.rule == "handoff"


# --------------------------------------------------------------------------
# History + context manager + guard_many
# --------------------------------------------------------------------------

class TestHistoryAndContext:
    def test_history_appends_one_per_span(self) -> None:
        gate = ProbeGate(probe="compile")
        for i in range(3):
            gate.guard(span(GOOD_CODE, 0.1, id=f"s{i}", step=i))
        assert len(gate.history) == 3
        assert all(isinstance(d, GateDecision) for d in gate.history)

    def test_context_manager(self) -> None:
        with ProbeGate(probe="compile") as gate:
            d = gate.guard(span(GOOD_CODE, 0.1))
        assert d.rule == "proceed"

    def test_guard_many_preserves_order(self) -> None:
        gate = ProbeGate(probe="compile")
        spans = [
            span(GOOD_CODE, 0.1, id="s1", step=1),
            span(BAD_CODE, 0.9, id="s2", step=2),
            span(GOOD_CODE, 0.9, id="s3", step=3),
        ]
        decisions = gate.guard_many(spans)
        assert [d.span_id for d in decisions] == ["s1", "s2", "s3"]
        assert [d.rule for d in decisions] == ["proceed", "handoff", "abstain"]


# --------------------------------------------------------------------------
# Probe registry
# --------------------------------------------------------------------------

class TestProbeRegistry:
    def test_all_four_probes_registered(self) -> None:
        assert set(PROBE_REGISTRY) == {"compile", "test", "lint", "schema"}

    def test_unknown_probe_raises(self) -> None:
        # an unknown probe name is rejected at ProbeGateConfig construction
        # (pydantic Literal — a ValueError subclass). The gate's own
        # PROBE_REGISTRY lookup is a defensive backstop for configs built
        # by other means.
        with pytest.raises(ValueError, match="probe"):
            ProbeGate(probe="nope")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Other probes (minimal-but-real: must return a ProbeResult without error)
# --------------------------------------------------------------------------

class TestOtherProbes:
    def test_lint_probe_flags_bare_except(self) -> None:
        gate = ProbeGate(probe="lint", uncertainty_threshold=0.5)
        code = "try:\n    x\nexcept:\n    pass\n"
        d = gate.guard(span(code, 0.9))
        assert d.probe is not None
        assert d.probe.passed is False
        assert "bare" in d.probe.evidence

    def test_test_probe_runs_embedded_tests(self) -> None:
        gate = ProbeGate(probe="test", uncertainty_threshold=0.5)
        code = (
            "def add(a, b):\n    return a + b\n"
            "def test_add():\n    assert add(2, 3) == 5\n"
        )
        d = gate.guard(span(code, 0.9))
        assert d.probe is not None
        assert d.probe.passed is True

    def test_test_probe_fails_on_assertion(self) -> None:
        gate = ProbeGate(probe="test", uncertainty_threshold=0.5)
        code = (
            "def add(a, b):\n    return a + b\n"
            "def test_add():\n    assert add(2, 3) == 6\n"
        )
        d = gate.guard(span(code, 0.9))
        assert d.probe is not None
        assert d.probe.passed is False

    def test_schema_probe_checks_required_keys(self) -> None:
        gate = ProbeGate(probe="schema", uncertainty_threshold=0.5)
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint", "method"], "types": {"endpoint": "string", "method": "string"}}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat", "method": "POST"}\n'
            "```\n"
        )
        d = gate.guard(span(code, 0.9))
        assert d.probe is not None
        assert d.probe.passed is True

    def test_schema_probe_fails_on_missing_key(self) -> None:
        gate = ProbeGate(probe="schema", uncertainty_threshold=0.5)
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint", "method"]}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat"}\n'
            "```\n"
        )
        d = gate.guard(span(code, 0.9))
        assert d.probe is not None
        assert d.probe.passed is False
        assert "method" in d.probe.evidence


# --------------------------------------------------------------------------
# Config round-trip
# --------------------------------------------------------------------------

class TestConfig:
    def test_config_defaults(self) -> None:
        cfg = ProbeGateConfig()
        assert cfg.uncertainty_threshold == 0.5
        assert cfg.probe == "compile"
        assert cfg.model_target == "deepseek-coder"

    def test_config_clamps_via_pydantic_validation(self) -> None:
        with pytest.raises(Exception):
            ProbeGateConfig(uncertainty_threshold=1.5)
