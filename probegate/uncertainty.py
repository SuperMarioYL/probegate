"""Uncertainty adapter — reads the model's self-reported uncertainty.

m1: the span carries its own ``uncertainty`` (from a 国产模型 API logprob the
agent already fetched, or a fixture in tests). The adapter returns it verbatim
and only normalizes into ``[0, 1]``.

m3: :meth:`UncertaintyAdapter.fetch_logprob` hits the OpenAI-compatible
``{base_url}/chat/completions`` endpoint of DeepSeek-Coder / Qwen3-Coder /
GLM with ``logprobs`` enabled and parses the per-token logprobs into a
``[0, 1]`` uncertainty (higher = less confident). The wedge is *exactly*
that 国产模型 calibration lags frontier closed models — so the adapter
exists to be the seam where the real logprob plugs in, never to *trust* the
number on its own (the AND with a probe is what makes the signal actionable).
"""
from __future__ import annotations

import math
from typing import Any

import httpx

from .models import ProbeGateConfig, Span


class UncertaintyConfigError(RuntimeError):
    """Raised when ``fetch_logprob`` cannot run — missing api_key/base_url."""


class UncertaintyFetchError(RuntimeError):
    """Raised when a 国产模型 logprob response cannot be parsed."""


def parse_logprob_uncertainty(data: dict[str, Any]) -> float:
    """Parse an OpenAI-compatible chat/completions logprob response into ``[0, 1]``.

    Higher = *less* confident. For each generated token we convert its top
    logprob to a probability ``p = exp(logprob)`` and take the per-token
    surprise ``1 - p``; the span uncertainty is the mean of those surprises.
    A perfectly confident model (every token logprob = 0.0) yields ``0.0``;
    a model that hedges every token trends toward ``1.0``.

    Expects the standard OpenAI shape::

        {"choices": [{"logprobs": {"content": [
            {"token": "def", "logprob": -0.1, ...}, ...
        ]}}]}
    """
    try:
        choices = data["choices"]
        logprobs_block = choices[0]["logprobs"]
        tokens = logprobs_block["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise UncertaintyFetchError(
            f"response missing choices[0].logprobs.content: {exc}"
        ) from exc
    if not tokens:
        raise UncertaintyFetchError("logprobs.content is empty — no tokens to score")

    surprises: list[float] = []
    # v0.4.0 (folded fix-parse-logprob-attributeerror): the per-token loop is
    # inside the try so a malformed (non-dict) token entry raises
    # UncertaintyFetchError instead of leaking a raw AttributeError. This is
    # latent today only while fetch_logprob is unwired; it goes live the moment
    # fix-guard-never-fetches-logprob lands, so the two ship together.
    try:
        for entry in tokens:
            logprob = entry.get("logprob")
            if logprob is None:
                continue
            # clamp: some backends emit +0.0 for forced/special tokens; a logprob
            # is a log-probability so it can never legitimately exceed 0.0.
            if logprob > 0.0:
                logprob = 0.0
            prob = math.exp(logprob)
            if prob > 1.0:
                prob = 1.0
            surprises.append(1.0 - prob)
    except (AttributeError, KeyError, TypeError) as exc:
        raise UncertaintyFetchError(
            f"malformed logprob token entry (expected dict with 'logprob'): {exc}"
        ) from exc
    if not surprises:
        raise UncertaintyFetchError("no token logprobs found in response")

    uncertainty = sum(surprises) / len(surprises)
    if uncertainty < 0.0:
        return 0.0
    if uncertainty > 1.0:
        return 1.0
    return uncertainty


class UncertaintyAdapter:
    """Reads/normalizes a span's self-reported uncertainty.

    m1: identity-ish (normalize into [0,1]). m3: hit the 国产模型 logprob API.
    """

    def __init__(
        self,
        config: ProbeGateConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or ProbeGateConfig()
        # test seam: an httpx MockTransport lets CI exercise the real fetch +
        # parse pipeline against a recorded response without live API keys.
        self._transport = transport

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

    async def fetch_logprob(self, span: Span) -> float:
        """Fetch the 国产模型 logprob-derived uncertainty for ``span``.

        Hits the OpenAI-compatible ``{base_url}/chat/completions`` endpoint of
        DeepSeek-Coder / Qwen3-Coder / GLM with ``logprobs`` enabled and parses
        the per-token logprobs into a ``[0, 1]`` uncertainty (higher = less
        confident). Requires ``config.api_key`` and ``config.base_url``;
        raises :class:`UncertaintyConfigError` otherwise so a caller on the m1
        path can fall back to :meth:`read` (``span.uncertainty``) instead of
        crashing the gate.
        """
        cfg = self.config
        if not cfg.api_key or not cfg.base_url:
            raise UncertaintyConfigError(
                "fetch_logprob requires config.api_key and config.base_url "
                "(国产模型 OpenAI-compatible endpoint); the m1 read(span) path "
                "uses span.uncertainty directly."
            )
        # v0.6.0 (fix-guard-fetch-non-json-crash): wrap the URL build, the
        # POST, and resp.json() so a json.JSONDecodeError (a 200 with a
        # non-JSON body — an HTML error page / empty / truncated stream, a
        # classic serving-tier hiccup) or a malformed-URL ValueError (a
        # scheme-less `api.deepseek.com/v1` typo in .probegate.toml) is
        # converted to UncertaintyFetchError. Both are ValueErrors that are
        # NONE of the types caught by _resolve_uncertainty /
        # _resolve_uncertainty_async in gate.py, so without this wrap they
        # would propagate through guard()/guard_async() and tear down the
        # agent loop — the exact cascade the v0.5.0 robustness fix was meant
        # to prevent. UncertaintyFetchError IS caught by those degrade paths.
        # parse_logprob_uncertainty raises UncertaintyFetchError (a
        # RuntimeError, not a ValueError) so it is intentionally left outside
        # this wrap.
        try:
            url = f"{cfg.base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": cfg.model_target,
                "messages": [{"role": "user", "content": span.content}],
                "logprobs": True,
                "top_logprobs": 1,
                "max_tokens": 16,
            }
            headers = {
                "Authorization": f"Bearer {cfg.api_key}",
                "Content-Type": "application/json",
            }
            client_kwargs: dict[str, Any] = {"timeout": 30.0}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except ValueError as exc:
            raise UncertaintyFetchError(
                "fetch_logprob could not read a JSON logprob response "
                f"(non-JSON 200 body or malformed base_url): {exc}"
            ) from exc
        return parse_logprob_uncertainty(data)
