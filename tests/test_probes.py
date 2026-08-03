"""Probe contract tests — pin that every advertised check actually fires.

These guard the false-negative class the v0.2.0 bug-hunter findings closed: a
probe advertises a check (the lint ``print`` check; the schema "typed"
evidence; the schema payload fallback) that silently did not run, so a
positive fixture passed with no violation. One contract test per advertised
behavior, on a positive fixture, so the class cannot silently recur.

Also pins the m3 UncertaintyAdapter logprob parse against a recorded response
so CI asserts the parse without live API keys.
"""
from __future__ import annotations

import asyncio
import json
import math

import httpx
import pytest

from probegate.models import ProbeGateConfig, Span
from probegate.probes.lint_probe import LintProbe
from probegate.probes.schema_probe import SchemaProbe
from probegate.uncertainty import (
    UncertaintyAdapter,
    UncertaintyConfigError,
    UncertaintyFetchError,
    parse_logprob_uncertainty,
)


def _span(content: str, *, id: str = "s", step: int = 1) -> Span:
    return Span(id=id, agent_step=step, content=content, uncertainty=0.5)


# --------------------------------------------------------------------------
# (a) LintProbe — the advertised print() check must fire
# --------------------------------------------------------------------------


class TestLintProbePrintContract:
    def test_stray_print_in_module_body_is_flagged(self) -> None:
        # The docstring advertises "print left in module body" as a violation;
        # before v0.2.0 the ast.walk loop never branched on ast.Call -> print,
        # so this fixture passed with "no lint violations" (false negative).
        code = "def f():\n    return 1\n\nprint('debug')\n"
        result = LintProbe().run(_span(code))
        assert result.probe == "lint"
        assert result.passed is False
        assert "print" in result.evidence

    def test_print_inside_function_is_also_flagged(self) -> None:
        # ast.walk visits every node; a print() anywhere is a stray print.
        code = "def f():\n    print('oops')\n    return 1\n"
        result = LintProbe().run(_span(code))
        assert result.passed is False
        assert "print" in result.evidence

    def test_attribute_print_is_not_flagged(self) -> None:
        # ``obj.print(...)`` is a method call, not the bare builtin — the
        # check uses getattr(node.func, "id", None), so an Attribute func
        # must not trip it (no false positive on the new branch).
        code = "class C:\n    def print(self, x):\n        pass\n\nc = C()\nc.print(1)\n"
        result = LintProbe().run(_span(code))
        # the bare-print branch must NOT fire on an attribute call
        assert "print" not in result.evidence
        assert result.passed is True

    def test_clean_code_still_passes(self) -> None:
        code = "def f():\n    return 1\n"
        result = LintProbe().run(_span(code))
        assert result.passed is True
        assert result.evidence == "no lint violations"


# --------------------------------------------------------------------------
# (b) SchemaProbe — unknown type must surface, never claim "typed"
# --------------------------------------------------------------------------


class TestSchemaProbeUnknownTypeContract:
    def test_unknown_type_appends_problem_and_fails(self) -> None:
        # Schema declares "datetime" — not in _TYPE_CHECK. Before v0.2.0 the
        # branch `if expected and expected in _TYPE_CHECK:` silently skipped,
        # returning passed=True with "... and typed" evidence (misleading).
        code = (
            "```probegate:schema\n"
            '{"required": ["ts"], "types": {"ts": "datetime"}}\n'
            "```\n"
            "```probegate:payload\n"
            '{"ts": "2026-08-04"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.probe == "schema"
        assert result.passed is False
        assert "datetime" in result.evidence
        # must not claim a type check that never ran
        assert "typed" not in result.evidence

    def test_known_type_still_type_checks_and_succeeds(self) -> None:
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint"], "types": {"endpoint": "string"}}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.passed is True
        assert "type-checked" in result.evidence

    def test_known_type_mismatch_fails(self) -> None:
        code = (
            "```probegate:schema\n"
            '{"required": ["port"], "types": {"port": "integer"}}\n'
            "```\n"
            "```probegate:payload\n"
            '{"port": "8080"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.passed is False
        assert "port" in result.evidence
        assert "integer" in result.evidence


# --------------------------------------------------------------------------
# (c) SchemaProbe — payload path succeeds; dropped fallback is honest
# --------------------------------------------------------------------------


class TestSchemaProbePayloadContract:
    def test_schema_plus_payload_succeeds(self) -> None:
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint", "method"], '
            '"types": {"endpoint": "string", "method": "string"}}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat", "method": "POST"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.probe == "schema"
        assert result.passed is True

    def test_schema_without_payload_fails_clearly(self) -> None:
        # v0.2.0 drops the broken whole-span JSON fallback (it always raised
        # JSONDecodeError on the still-fenced content). A schema block now
        # requires a payload block — an honest failure, not a silent pass.
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint"]}\n'
            "```\n"
            "some prose around it, not a payload block\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.passed is False
        assert "payload" in result.evidence


# --------------------------------------------------------------------------
# (feat) UncertaintyAdapter — real logprob parse contract (no live keys)
# --------------------------------------------------------------------------

RECORDED_LOGPROB_RESPONSE = {
    "id": "chatcmpl-recorded",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "def add("},
            "logprobs": {
                "content": [
                    {
                        "token": "def",
                        "logprob": -0.1,
                        "top_logprobs": [{"token": "def", "logprob": -0.1}],
                    },
                    {
                        "token": " add",
                        "logprob": -0.2,
                        "top_logprobs": [{"token": " add", "logprob": -0.2}],
                    },
                    {
                        "token": "(",
                        "logprob": 0.0,
                        "top_logprobs": [{"token": "(", "logprob": 0.0}],
                    },
                ]
            },
            "finish_reason": "length",
        }
    ],
}


class TestUncertaintyLogprobContract:
    def test_parse_returns_value_in_unit_interval(self) -> None:
        u = parse_logprob_uncertainty(RECORDED_LOGPROB_RESPONSE)
        assert 0.0 <= u <= 1.0

    def test_parse_value_is_mean_surprise(self) -> None:
        # uncertainty = mean(1 - exp(logprob)) over the three tokens
        expected = (
            (1.0 - math.exp(-0.1))
            + (1.0 - math.exp(-0.2))
            + (1.0 - math.exp(0.0))
        ) / 3
        u = parse_logprob_uncertainty(RECORDED_LOGPROB_RESPONSE)
        assert abs(u - expected) < 1e-9

    def test_parse_perfect_confidence_is_zero(self) -> None:
        data = {
            "choices": [
                {
                    "logprobs": {
                        "content": [
                            {"token": "x", "logprob": 0.0},
                            {"token": "y", "logprob": 0.0},
                        ]
                    }
                }
            ]
        }
        assert parse_logprob_uncertainty(data) == 0.0

    def test_parse_clamps_positive_logprob(self) -> None:
        # some backends emit +0.0 / tiny positive for forced tokens; the
        # clamp must keep surprise at 0.0 rather than going negative.
        data = {
            "choices": [
                {
                    "logprobs": {
                        "content": [
                            {"token": "x", "logprob": 0.0},
                            {"token": "y", "logprob": 1e-9},
                        ]
                    }
                }
            ]
        }
        u = parse_logprob_uncertainty(data)
        assert u == 0.0

    def test_parse_missing_logprobs_raises(self) -> None:
        with pytest.raises(UncertaintyFetchError):
            parse_logprob_uncertainty({"choices": [{}]})

    def test_parse_empty_content_raises(self) -> None:
        with pytest.raises(UncertaintyFetchError):
            parse_logprob_uncertainty(
                {"choices": [{"logprobs": {"content": []}}]}
            )

    def test_fetch_logprob_requires_creds(self) -> None:
        # no api_key / base_url on the default config -> config error, no
        # network attempted (so the m1 path can fall back to read() safely).
        adapter = UncertaintyAdapter(ProbeGateConfig())
        with pytest.raises(UncertaintyConfigError):
            asyncio.run(adapter.fetch_logprob(_span("hi")))

    def test_fetch_logprob_uses_recorded_response(self) -> None:
        # exercise the real httpx call -> parse pipeline against a mocked
        # transport, so CI asserts the fetch path without live API keys.
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["authorization"] == "Bearer test-key"
            body = json.loads(request.content)
            assert body["model"] == "deepseek-coder"
            assert body["logprobs"] is True
            assert body["top_logprobs"] == 1
            return httpx.Response(200, json=RECORDED_LOGPROB_RESPONSE)

        cfg = ProbeGateConfig(
            api_key="test-key",
            base_url="https://api.deepseek.com/v1",
            model_target="deepseek-coder",
        )
        adapter = UncertaintyAdapter(cfg, transport=httpx.MockTransport(handler))
        u = asyncio.run(adapter.fetch_logprob(_span("def add(a, b):")))
        assert 0.0 <= u <= 1.0
        # same value as the direct parse — the fetch is parse_logprob_uncertainty
        assert abs(u - parse_logprob_uncertainty(RECORDED_LOGPROB_RESPONSE)) < 1e-12

    def test_fetch_logprob_raises_on_http_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key"})

        cfg = ProbeGateConfig(
            api_key="bad",
            base_url="https://api.deepseek.com/v1",
            model_target="deepseek-coder",
        )
        adapter = UncertaintyAdapter(cfg, transport=httpx.MockTransport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(adapter.fetch_logprob(_span("hi")))
