"""ProbeGate — per-span probe-validation gate for 国产模型 autonomous agents.

ProbeGate pairs a model's self-reported uncertainty with a machine-verifiable
probe (compile/test/lint/schema) and routes to a human only when BOTH signals
fire: ``handoff <=> uncertainty > tau AND not probe.passed``.
"""
from .models import Span, ProbeResult, GateDecision, ProbeGateConfig
from .gate import ProbeGate
from .uncertainty import UncertaintyAdapter

__version__ = "0.1.0"

__all__ = [
    "Span",
    "ProbeResult",
    "GateDecision",
    "ProbeGateConfig",
    "ProbeGate",
    "UncertaintyAdapter",
    "__version__",
]
