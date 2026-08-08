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

import argparse
import asyncio
import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient

from probegate.audit import LocalAuditLog
from probegate.cli import _load_config, _resolve_gate_config, main
from probegate.gate import ProbeGate
from probegate.models import ProbeGateConfig, Span
from probegate.probes.lint_probe import LintProbe
from probegate.probes.schema_probe import SchemaProbe
from probegate.uncertainty import (
    UncertaintyAdapter,
    UncertaintyConfigError,
    UncertaintyFetchError,
    parse_logprob_uncertainty,
)
from probegate.ui.web import app, set_audit_log


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


# ==========================================================================
# v0.3.0 — fix-init-config-never-read: .probegate.toml values flow into the
# gate, and explicit CLI flags win over the config file.
# ==========================================================================


def _ns(**kwargs: object) -> argparse.Namespace:
    """An argparse.Namespace with the gate-config flags all defaulting to None."""
    defaults = {
        "threshold": None,
        "probe": None,
        "model": None,
        "api_key": None,
        "base_url": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestInitConfigLoadContract:
    def test_load_config_returns_none_when_absent(self, tmp_path: object) -> None:
        assert _load_config(f"{tmp_path}/.probegate.toml") is None

    def test_init_writes_then_load_reads_back_values(self, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
        # the documented happy path: init -> edit api_key/base_url -> load
        monkeypatch.chdir(str(tmp_path))
        rc = main([
            "init", "--threshold", "0.2", "--probe", "lint",
            "--model", "qwen3-coder", "--api-key", "k", "--base-url", "https://x/v1",
        ])
        assert rc == 0
        cfg = _load_config(f"{tmp_path}/.probegate.toml")
        assert cfg is not None
        assert cfg.uncertainty_threshold == 0.2
        assert cfg.probe == "lint"
        assert cfg.model_target == "qwen3-coder"
        assert cfg.api_key == "k"
        assert cfg.base_url == "https://x/v1"

    def test_toml_values_flow_into_probe_gate(self, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
        # the v0.2.0 flagship logprob feature was unreachable end-to-end because
        # no subcommand read .probegate.toml back. Now api_key/base_url flow in.
        monkeypatch.chdir(str(tmp_path))
        main(["init", "--threshold", "0.2", "--probe", "lint", "--api-key", "k", "--base-url", "https://x/v1"])
        base = _load_config(f"{tmp_path}/.probegate.toml")
        assert base is not None
        gate = ProbeGate(config=base)
        assert gate.config.uncertainty_threshold == 0.2
        assert gate.config.probe == "lint"
        assert gate.config.api_key == "k"
        assert gate.config.base_url == "https://x/v1"
        # the gate's uncertainty adapter sees the same config (m3 creds reachable)
        assert gate.adapter.config.api_key == "k"

    def test_flag_wins_over_config(self, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(str(tmp_path))
        main(["init", "--threshold", "0.2", "--probe", "lint"])
        base = _load_config(f"{tmp_path}/.probegate.toml")
        # explicit --threshold flag overrides config's 0.2; --probe left None
        # falls back to the config's "lint".
        cfg = _resolve_gate_config(_ns(threshold=0.7), base=base)
        assert cfg.uncertainty_threshold == 0.7
        assert cfg.probe == "lint"

    def test_no_config_no_flag_uses_defaults(self, tmp_path: object) -> None:
        base = _load_config(f"{tmp_path}/.probegate.toml")  # None — no file
        cfg = _resolve_gate_config(_ns(), base=base)
        assert cfg.uncertainty_threshold == 0.5
        assert cfg.probe == "compile"
        assert cfg.model_target == "deepseek-coder"

    def test_config_flag_override_sets_creds(self, tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
        # --api-key/--base-url on a subcommand reach the gate even with no toml
        base = _load_config(f"{tmp_path}/.probegate.toml")  # None
        cfg = _resolve_gate_config(_ns(api_key="flag-key", base_url="https://y/v1"), base=base)
        assert cfg.api_key == "flag-key"
        assert cfg.base_url == "https://y/v1"


# ==========================================================================
# v0.3.0 — fix-schema-probe-malformed-schema: validate the schema block's
# shape (required=list, types=dict) instead of crashing or mis-parsing.
# ==========================================================================


class TestSchemaProbeMalformedShapeContract:
    def test_array_form_types_does_not_crash(self) -> None:
        # JSON-Schema array form: types is a list, not a dict. Before v0.3.0
        # types.get(key) raised AttributeError (list has no .get) -> raw traceback.
        code = (
            "```probegate:schema\n"
            '{"required": ["x"], "types": ["string"]}\n'
            "```\n"
            "```probegate:payload\n"
            '{"x": "v"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.probe == "schema"
        assert result.passed is False
        assert "malformed" in result.evidence

    def test_string_form_required_does_not_iterate_chars(self) -> None:
        # {"required": "endpoint"} — required is a string, not a list. Before
        # v0.3.0 the for-loop iterated characters, emitting misleading evidence
        # "missing required key e; n; ...; t" (false negative with misleading
        # evidence — the exact class v0.2.0 closed for unknown types).
        code = (
            "```probegate:schema\n"
            '{"required": "endpoint"}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.probe == "schema"
        assert result.passed is False
        assert "malformed" in result.evidence
        assert "missing required key e" not in result.evidence

    def test_well_shaped_schema_still_validates(self) -> None:
        # regression: the new shape guard must not break the happy path
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

    def test_schema_with_no_types_field_still_ok(self) -> None:
        # types absent -> defaults to {} -> isinstance({}, dict) is True -> no
        # false malformed failure. Pins the absent-field path.
        code = (
            "```probegate:schema\n"
            '{"required": ["endpoint"]}\n'
            "```\n"
            "```probegate:payload\n"
            '{"endpoint": "/v1/chat"}\n'
            "```\n"
        )
        result = SchemaProbe().run(_span(code))
        assert result.passed is True


# ==========================================================================
# v0.3.0 — fix-web-guard-validation-500: /api/guard returns 422 (not 500) for
# a bad probe name / out-of-range threshold / bad model_target.
# ==========================================================================


def _web_span(span_id: str = "s", step: int = 1) -> dict[str, object]:
    return {"id": span_id, "agent_step": step, "content": "x = 1\n", "uncertainty": 0.1}


class TestWebGuardValidationContract:
    def test_bad_probe_name_returns_422_not_500(self, tmp_path: object) -> None:
        set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))
        client = TestClient(app)
        body = {"spans": [_web_span()], "probe": "nope", "uncertainty_threshold": 0.5, "model_target": "deepseek-coder"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 422

    def test_out_of_range_threshold_returns_422_not_500(self, tmp_path: object) -> None:
        set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))
        client = TestClient(app)
        body = {"spans": [_web_span()], "probe": "compile", "uncertainty_threshold": 99.0, "model_target": "deepseek-coder"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 422

    def test_negative_threshold_returns_422(self, tmp_path: object) -> None:
        set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))
        client = TestClient(app)
        body = {"spans": [_web_span()], "probe": "compile", "uncertainty_threshold": -0.1, "model_target": "deepseek-coder"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 422

    def test_bad_model_target_returns_422(self, tmp_path: object) -> None:
        set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))
        client = TestClient(app)
        body = {"spans": [_web_span()], "probe": "compile", "uncertainty_threshold": 0.5, "model_target": "gpt-4"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 422

    def test_valid_body_returns_200(self, tmp_path: object) -> None:
        set_audit_log(LocalAuditLog(path=f"{tmp_path}/audit.jsonl"))
        client = TestClient(app)
        body = {"spans": [_web_span()], "probe": "compile", "uncertainty_threshold": 0.5, "model_target": "deepseek-coder"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 200
        assert "decisions" in r.json()


# ==========================================================================
# v0.3.0 — feat-local-audit-log-stub: each GateDecision + operator action is
# appended to the local audit jsonl (OSS, NOT the Team paid export).
# ==========================================================================


class TestLocalAuditLogContract:
    def test_append_decision_writes_jsonl(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        d = gate.guard(Span(id="s1", agent_step=1, content="x = 1\n", uncertainty=0.1))
        log.append_decision(d)
        records = log.read()
        assert len(records) == 1
        assert records[0]["type"] == "decision"
        assert records[0]["span_id"] == "s1"
        assert records[0]["rule"] == "proceed"
        assert "probe" in records[0]
        assert "uncertainty" in records[0]

    def test_append_many_writes_one_line_each(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        decisions = [
            gate.guard(Span(id=f"s{i}", agent_step=i, content="x = 1\n", uncertainty=0.1))
            for i in range(3)
        ]
        log.append_decision_many(decisions)
        records = log.read()
        assert len(records) == 3
        assert [r["span_id"] for r in records] == ["s0", "s1", "s2"]

    def test_append_action_writes_jsonl(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        log.append_action("s1", "approved", "proceed")
        records = log.read()
        assert len(records) == 1
        assert records[0] == {
            "type": "action", "span_id": "s1", "action": "approved", "rule": "proceed",
        }

    def test_guard_endpoint_appends_decisions(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        set_audit_log(log)
        client = TestClient(app)
        body = {"spans": [_web_span("s1")], "probe": "compile", "uncertainty_threshold": 0.5, "model_target": "deepseek-coder"}
        r = client.post("/api/guard", json=body)
        assert r.status_code == 200
        records = log.read()
        assert len(records) == 1
        assert records[0]["type"] == "decision"
        assert records[0]["span_id"] == "s1"

    def test_demo_endpoint_appends_decisions(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        set_audit_log(log)
        client = TestClient(app)
        r = client.get("/api/demo")
        assert r.status_code == 200
        records = log.read()
        # the built-in demo agent has 5 spans => 5 decision records
        assert len(records) == 5
        assert all(rec["type"] == "decision" for rec in records)

    def test_approve_and_reject_persist_actions(self, tmp_path: object) -> None:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        set_audit_log(log)
        client = TestClient(app)
        client.post("/api/approve?span_id=s4")
        client.post("/api/reject?span_id=s5")
        records = log.read()
        assert len(records) == 2
        assert records[0]["action"] == "approved"
        assert records[0]["rule"] == "proceed"
        assert records[1]["action"] == "rejected"
        assert records[1]["rule"] == "rewind"

    def test_default_path_is_probegate_audit_jsonl(self) -> None:
        # the DEFAULT_AUDIT_PATH is the OSS-local location, NOT a Team export
        from probegate.audit import DEFAULT_AUDIT_PATH
        assert str(DEFAULT_AUDIT_PATH) == ".probegate/audit.jsonl"
