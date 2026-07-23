"""Pydantic data models for ProbeGate.

The core primitive is the *dual-signal abstention gate*. Three value objects
carry the gate state:

* :class:`Span` — one step of an agent trajectory, with the model's
  self-reported uncertainty (0..1, sourced from 国产模型 logprobs).
* :class:`ProbeResult` — the verdict of a machine-checkable probe
  (compile/test/lint/schema) plus human-readable evidence.
* :class:`GateDecision` — the gate's three-state verdict
  (``proceed`` / ``abstain`` / ``handoff``) with rationale.

Key invariant (see ``probegate/gate.py``)::

    handoff <=> uncertainty > tau AND not probe.passed
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProbeKind = Literal["compile", "test", "lint", "schema"]
GateRule = Literal["proceed", "abstain", "handoff"]
ModelTarget = Literal["deepseek-coder", "qwen3-coder", "glm-5"]


class Span(BaseModel):
    """One span of an agent trajectory.

    ``uncertainty`` is the model's self-reported confidence in this span,
    in ``[0, 1]``, sourced from the 国产模型 serving tier's logprobs. Higher
    means *less* confident. It is intentionally a black-box signal on its own;
    the gate refuses to act on it without a probe.
    """

    id: str
    agent_step: int
    content: str
    uncertainty: float = Field(ge=0.0, le=1.0, description="model self-reported uncertainty, 0..1")


class ProbeResult(BaseModel):
    """The verdict of a machine-checkable probe over a span."""

    probe: ProbeKind
    passed: bool
    evidence: str = ""


class GateDecision(BaseModel):
    """The gate's verdict for a span.

    * ``proceed`` — both signals clear (confident AND probe passes).
    * ``abstain`` — exactly one signal trips; the gate flags the span but
      does *not* route to a human. The agent proceeds.
    * ``handoff`` — both signals fire (uncertain AND probe fails). The gate
      routes the span to a human before the agent commits it.
    """

    span_id: str
    rule: GateRule
    rationale: str
    probe: ProbeResult | None = None
    uncertainty: float | None = None


class ProbeGateConfig(BaseModel):
    """Configuration for a :class:`ProbeGate` instance.

    Written to ``.probegate.toml`` by ``probegate init``.
    """

    uncertainty_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="tau — spans above this AND a failing probe => handoff"
    )
    probe: ProbeKind = Field(default="compile", description="which machine-checkable probe to run")
    model_target: str = Field(default="deepseek-coder", description="国产模型 serving target")
    handoff_mode: Literal["cli", "web"] = Field(default="cli", description="where the human gate surfaces")
    api_key: str | None = Field(default=None, description="国产模型 API key (m3: logprob fetch)")
    base_url: str | None = Field(
        default=None, description="国产模型 OpenAI-compatible base URL (m3: logprob fetch)"
    )
