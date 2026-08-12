"""v0.4.0 regression contract — the m3 logprob fetch is wired into the gate runtime.

Bug fixed (fix-guard-never-fetches-logprob, high): ``ProbeGate.guard()`` only
ever called ``adapter.read(span)`` (the m1 identity that returns
``span.uncertainty`` verbatim) and never ``UncertaintyAdapter.fetch_logprob()``
— the real httpx logprob fetch completed in v0.2.0. So the v0.3.0
fix-init-config-never-read claim of making the logprob feature "reachable
end-to-end" was not delivered as shipped; the dual-signal gate's uncertainty
half was still the un-trusted self-report the product thesis says not to trust.

The fix wires the existing fetch into the runtime: ``guard()`` now drives
``fetch_logprob()`` when ``config.api_key`` and ``config.base_url`` are set,
falling back to ``read()`` on ``UncertaintyConfigError`` (creds-less callers
keep m1 behaviour). ``/api/guard`` also stops rebuilding a config that drops
the loaded creds.

Folded fix (fix-parse-logprob-attributeerror, medium — latent until the fetch
lands): ``parse_logprob_uncertainty`` raises ``UncertaintyFetchError`` (not a
raw ``AttributeError``) on non-dict token entries.

Each distinguishing test below FAILS on pre-fix code and PASSES on the fixed
code (verified by reasoning over the pre-fix ``guard()`` body that called
``self.adapter.read(span)`` unconditionally).
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from probegate.audit import LocalAuditLog
from probegate.gate import ProbeGate
from probegate.models import ProbeGateConfig, Span
from probegate.uncertainty import (
    UncertaintyAdapter,
    UncertaintyConfigError,
    UncertaintyFetchError,
    parse_logprob_uncertainty,
)
from probegate.ui import web


# A recorded OpenAI-compatible chat/completions logprob response (the same shape
# the v0.2.0 adapter contract test pins). Its parsed uncertainty is the mean
# per-token surprise over logprobs [-0.1, -0.2, 0.0] ≈ 0.0921 — deliberately
# different from any hard-coded span.uncertainty we feed the gate so a fetch
# (fixed) and a read() (pre-fix) are distinguishable by value.
RECORDED_LOGPROB_RESPONSE = {
    "id": "chatcmpl-recorded",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "def add("},
            "logprobs": {
                "content": [
                    {"token": "def", "logprob": -0.1},
                    {"token": " add", "logprob": -0.2},
                    {"token": "(", "logprob": 0.0},
                ]
            },
            "finish_reason": "length",
        }
    ],
}

# span.uncertainty the demo/hard-coded path would return verbatim; the fetched
# path must NOT equal this.
HARDCODED_UNCERTAINTY = 0.5


def _creds_cfg() -> ProbeGateConfig:
    return ProbeGateConfig(
        uncertainty_threshold=0.5,
        probe="compile",
        model_target="deepseek-coder",
        api_key="test-key",
        base_url="https://api.deepseek.com/v1",
    )


def _mock_handler(fetched: list[int]) -> httpx.MockTransport:
    """An httpx MockTransport that records the hit and returns the recorded body."""

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(1)
        # assert the fetch actually wired the configured creds + endpoint
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=RECORDED_LOGPROB_RESPONSE)

    return httpx.MockTransport(handler)


# ==========================================================================
# (1) Core: guard() drives fetch_logprob when creds are set and uses the
#     fetched value (not the span's hard-coded uncertainty).
# ==========================================================================


class TestGuardFetchLogprobWiredContract:
    def test_guard_fetches_logprob_when_creds_set_uses_fetched_value(self) -> None:
        # Pre-fix guard() called self.adapter.read(span) unconditionally -> the
        # mock handler would NEVER be hit and decision.uncertainty would equal
        # the hard-coded 0.5. Fixed guard() drives fetch_logprob -> the handler
        # is hit once and decision.uncertainty equals the parsed logprob.
        fetched: list[int] = []
        cfg = _creds_cfg()
        adapter = UncertaintyAdapter(cfg, transport=_mock_handler(fetched))
        gate = ProbeGate(config=cfg, uncertainty_adapter=adapter)
        span = Span(
            id="s1",
            agent_step=1,
            content="def add(a, b):",
            uncertainty=HARDCODED_UNCERTAINTY,
        )

        decision = gate.guard(span)

        # THE wire assertion: the m3 fetch path was actually invoked end-to-end.
        assert len(fetched) == 1
        expected = parse_logprob_uncertainty(RECORDED_LOGPROB_RESPONSE)
        # the resolved uncertainty reflects the fetched logprob, not the
        # span's hard-coded self-report
        assert decision.uncertainty == pytest.approx(expected)
        assert decision.uncertainty != HARDCODED_UNCERTAINTY

    def test_guard_falls_back_to_read_on_uncertainty_config_error(self) -> None:
        # The fix's defensive contract: if fetch_logprob raises
        # UncertaintyConfigError (e.g. a subclass whose creds check fails at
        # fetch time), guard() must fall back to read(span) and NOT crash.
        # Distinguishes pre-fix because pre-fix never calls fetch_logprob at
        # all (so fetch_called stays False).
        class _ConfigErrAdapter(UncertaintyAdapter):
            fetch_called = False

            async def fetch_logprob(self, span: Span) -> float:  # noqa: ARG002
                type(self).fetch_called = True
                raise UncertaintyConfigError("simulated fetch-time config error")

        cfg = _creds_cfg()
        gate = ProbeGate(config=cfg, uncertainty_adapter=_ConfigErrAdapter(cfg))
        span = Span(
            id="s2",
            agent_step=2,
            content="x = 1\n",
            uncertainty=HARDCODED_UNCERTAINTY,
        )

        decision = gate.guard(span)

        # the fix attempted a fetch (pre-fix would not have)
        assert _ConfigErrAdapter.fetch_called is True
        # and fell back to the m1 read() value without crashing
        assert decision.uncertainty == HARDCODED_UNCERTAINTY


# ==========================================================================
# (2) /api/guard carries the loaded creds (_default_config's api_key/base_url)
#     into the rebuilt gate config so they actually drive a fetch end-to-end.
#     Pre-fix the rebuild dropped the creds -> /api/guard never fetched.
# ==========================================================================


class TestApiGuardCarriesCredsContract:
    def test_api_guard_carries_loaded_creds_and_drives_fetch(
        self,
        tmp_path: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fetched: list[int] = []

        # Inject a mock transport into every adapter the endpoint constructs by
        # patching the UncertaintyAdapter name the gate module looks up. This
        # tests the REAL /api/guard -> ProbeGate -> adapter -> httpx path; the
        # only substitute is the network transport (no live API key in CI).
        class _MockTransportAdapter(UncertaintyAdapter):
            def __init__(self, config: ProbeGateConfig | None = None, **kwargs: object) -> None:
                super().__init__(config, transport=_mock_handler(fetched))

        monkeypatch.setattr("probegate.gate.UncertaintyAdapter", _MockTransportAdapter)
        # operator has configured creds via `probegate init` -> _default_config
        monkeypatch.setattr(
            web,
            "_default_config",
            ProbeGateConfig(
                api_key="test-key",
                base_url="https://api.deepseek.com/v1",
                model_target="deepseek-coder",
            ),
        )
        web.set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))

        client = TestClient(web.app)
        body = {
            "spans": [
                {
                    "id": "s1",
                    "agent_step": 1,
                    "content": "def add(a, b):",
                    "uncertainty": HARDCODED_UNCERTAINTY,
                }
            ],
            "probe": "compile",
            "uncertainty_threshold": 0.5,
            "model_target": "deepseek-coder",
        }
        r = client.post("/api/guard", json=body)
        assert r.status_code == 200
        decisions = r.json()["decisions"]
        assert len(decisions) == 1

        # the creds carried through the rebuild -> the gate fetched a logprob
        assert len(fetched) == 1
        expected = parse_logprob_uncertainty(RECORDED_LOGPROB_RESPONSE)
        assert decisions[0]["uncertainty"] == pytest.approx(expected)
        assert decisions[0]["uncertainty"] != HARDCODED_UNCERTAINTY


# ==========================================================================
# (3) Folded fix-parse-logprob-attributeerror: non-dict token entries raise
#     UncertaintyFetchError, not a raw AttributeError. Latent until the fetch
#     lands (test 1), so the two ship together.
# ==========================================================================


class TestParseLogprobNonDictTokenContract:
    def test_non_dict_token_entries_raise_fetch_error_not_attribute_error(self) -> None:
        # A 国产模型 OpenAI-compatible backend may emit logprobs.content as a
        # list of bare strings (e.g. ["def", " add"]). Pre-fix the per-token
        # loop did entry.get("logprob") outside the try -> raw AttributeError;
        # fixed it raises UncertaintyFetchError per the documented contract.
        data = {"choices": [{"logprobs": {"content": ["def", " add"]}}]}
        with pytest.raises(UncertaintyFetchError):
            parse_logprob_uncertainty(data)
