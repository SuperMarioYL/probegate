"""Uncertainty adapter — reads the model's self-reported uncertainty.

m1: the span carries its own ``uncertainty`` (from a 国产模型 API logprob the
agent already fetched, or a fixture in tests). The adapter returns it verbatim
and only normalizes into ``[0, 1]``.

m3 (TODO): wire to the OpenAI-compatible logprob endpoints of DeepSeek-Coder /
Qwen3-Coder / GLM, so the gate can fetch uncertainty itself rather than trust
the caller. The wedge is *exactly* that 国产模型 calibration lags frontier
closed models — so the adapter exists to be the seam where the real logprob
plugs in, never to *trust* the number on its own (the AND with a probe is what
makes the signal actionable).
"""
from __future__ import annotations

from .models import ProbeGateConfig, Span


class UncertaintyAdapter:
    """Reads/normalizes a span's self-reported uncertainty.

    m1: identity-ish (normalize into [0,1]). m3: hit the 国产模型 logprob API.
    """

    def __init__(self, config: ProbeGateConfig | None = None) -> None:
        self.config = config or ProbeGateConfig()

    def read(self, span: Span) -> float:
        """Return the span's uncertainty, clamped to ``[0, 1]``.

        m1: the caller has already attached the logprob-derived uncertainty to
        the span. We do NOT trust this number alone — the gate ANDs it with a
        probe precisely because self-reported calibration is a black box.
        """
        u = float(span.uncertainty)
        if u < 0.0:
            return 0.0
        if u > 1.0:
            return 1.0
        return u

    # m3 placeholder — real 国产模型 logprob fetch goes here. Kept as an
    # explicit stub so the seam is visible; do NOT call before m3 lands.
    async def fetch_logprob(self, span: Span) -> float:  # noqa: ARG002
        raise NotImplementedError(
            "logprob fetch from 国产模型 serving tier is the m3 milestone; "
            "m1 reads span.uncertainty directly."
        )
