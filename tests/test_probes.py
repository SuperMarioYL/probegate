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
from probegate.cli import _load_config, _resolve_gate_config, _toml_dump, main
from probegate.gate import ProbeGate
from probegate.models import ProbeGateConfig, Span
from probegate.probes.lint_probe import LintProbe
from probegate.probes.schema_probe import SchemaProbe
from probegate.probes.test_probe import TestProbe
from probegate.uncertainty import (
    UncertaintyAdapter,
    UncertaintyConfigError,
    UncertaintyFetchError,
    parse_logprob_uncertainty,
)
from probegate.ui.web import app, set_audit_log, set_default_config


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

    def test_read_degrades_on_corrupt_last_line(self, tmp_path: object) -> None:
        # v0.7.0 fix-audit-log-read-corrupt-line-crash (re-landed on main in
        # v0.8.0 by fix-release-branch-drift): the audit log is append-only, so
        # a process killed mid-write (or an OS-level partial write of a large
        # record, or a hand-edited file) leaves a truncated/corrupt last line.
        # Pre-v0.7.0 read() did a bare json.loads(line) and raised
        # json.JSONDecodeError, hiding the salvageable records. Fixed: the
        # corrupt line is skipped and the salvageable records are returned
        # (degrade, not crash — the same posture the v0.5.0/v0.6.0 fetch fixes
        # established).
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        gate = ProbeGate(probe="compile", uncertainty_threshold=0.5)
        # one valid record
        log.append_decision(gate.guard(Span(id="s1", agent_step=1, content="x = 1\n", uncertainty=0.1)))
        # simulate a process killed mid-write leaving a truncated last line
        # (partial JSON, no trailing newline — the exact byte state a SIGKILL
        # mid-write leaves)
        with open(f"{tmp_path}/audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write('{"type": "decision", "span_id": "s2", "rule": "proce')
        # read() must NOT raise; it returns the one salvageable valid record
        records = log.read()
        assert len(records) == 1
        assert records[0]["type"] == "decision"
        assert records[0]["span_id"] == "s1"

    def test_read_fully_corrupt_file_returns_empty(self, tmp_path: object) -> None:
        # a file with only corrupt lines degrades to [] rather than raising.
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        with open(f"{tmp_path}/audit.jsonl", "w", encoding="utf-8") as fh:
            fh.write("not json at all\n{also broken\n")
        assert log.read() == []


# --------------------------------------------------------------------------
# (e) SchemaProbe — CRLF-authored fences must not silently skip validation
#     (v0.8.0 fix-schema-probe-crlf-fence-false-negative)
# --------------------------------------------------------------------------

class TestSchemaProbeCrlfFenceContract:
    """The fence regex required a literal ``\\n`` after the fence label, so a
    span whose ``probegate:schema`` / ``probegate:payload`` blocks used Windows
    CRLF line endings never matched: the probe silently PASSED with "no
    ``probegate:schema`` block" and a schema-violating span escaped the human
    gate. These tests pin that CRLF fences are parsed and validated exactly
    like LF ones."""

    CRLF_SCHEMA = (
        "```probegate:schema\r\n"
        '{"required": ["endpoint", "method"], "types": {"endpoint": "string"}}\r\n'
        "```\r\n"
        "```probegate:payload\r\n"
        '{"method": 12345}\r\n'
        "```\r\n"
    )

    def test_crlf_fences_are_parsed_and_violations_fail(self) -> None:
        # payload misses the required key `endpoint` and `method` is not a
        # string — the probe must FAIL with the violation, not claim "no
        # schema block".
        result = SchemaProbe().run(_span(self.CRLF_SCHEMA))
        assert result.passed is False
        assert "missing required key `endpoint`" in result.evidence
        assert "no `probegate:schema` block" not in result.evidence

    def test_crlf_conformant_payload_passes(self) -> None:
        good = (
            "```probegate:schema\r\n"
            '{"required": ["endpoint", "method"], "types": {"endpoint": "string"}}\r\n'
            "```\r\n"
            "```probegate:payload\r\n"
            '{"endpoint": "/v1/chat", "method": "POST"}\r\n'
            "```\r\n"
        )
        result = SchemaProbe().run(_span(good))
        assert result.passed is True
        assert "2 required key(s) present" in result.evidence

    def test_crlf_span_through_gate_routes_to_handoff(self) -> None:
        # end-to-end regression: pre-v0.8.0 this span (uncertainty 0.90 > tau
        # 0.50 AND a failing probe) emitted abstain — the violating span
        # escaped the human gate purely because of its line endings.
        gate = ProbeGate(config=ProbeGateConfig(uncertainty_threshold=0.5, probe="schema"))
        span = Span(id="s-crlf", agent_step=1, content=self.CRLF_SCHEMA, uncertainty=0.9)
        decision = gate.guard(span)
        assert decision.rule == "handoff"
        assert decision.probe is not None and decision.probe.passed is False


# --------------------------------------------------------------------------
# (f) Web UI demo flow — decisions must not be double-logged into the local
#     audit log (v0.9.0 fix-web-ui-double-audit)
# --------------------------------------------------------------------------

class TestWebDemoAuditContract:
    """gate.html's run() calls GET /api/demo (only to fetch the demo spans)
    and then POST /api/guard (whose decisions are the ones rendered and
    actioned). Pre-v0.9.0 BOTH endpoints appended decisions, so one UI demo
    run left 10 decision records in audit.jsonl for 5 spans."""

    def _reset(self, tmp_path: object) -> LocalAuditLog:
        log = LocalAuditLog(path=f"{tmp_path}/audit.jsonl")
        set_audit_log(log)
        set_default_config(None)
        return log

    def test_exact_ui_flow_logs_each_decision_once(self, tmp_path: object) -> None:
        client = TestClient(app)
        log = self._reset(tmp_path)
        demo = client.get("/api/demo?audit=false")
        client.post("/api/guard", json={"spans": demo.json()["spans"]})
        records = [r for r in log.read() if r.get("type") == "decision"]
        assert len(records) == 5

    def test_demo_audit_false_appends_nothing(self, tmp_path: object) -> None:
        client = TestClient(app)
        log = self._reset(tmp_path)
        resp = client.get("/api/demo?audit=false")
        assert resp.status_code == 200
        assert len(resp.json()["spans"]) == 5
        assert len(resp.json()["decisions"]) == 5
        assert log.read() == []

    def test_demo_default_audit_true_still_appends(self, tmp_path: object) -> None:
        # the documented /api/demo contract (decisions appended) is unchanged
        client = TestClient(app)
        log = self._reset(tmp_path)
        client.get("/api/demo")
        records = [r for r in log.read() if r.get("type") == "decision"]
        assert len(records) == 5


# --------------------------------------------------------------------------
# (g) _toml_dump escaping — an init-written config must round-trip
#     (v0.9.0 fix-init-toml-injection)
# --------------------------------------------------------------------------

class TestTomlDumpEscapingContract:
    """_toml_dump used to interpolate api_key/base_url into TOML basic strings
    with no escaping: an api_key containing a double-quote produced a
    .probegate.toml that tomllib refuses to parse, and a key containing a
    backslash was silently corrupted on read-back (\\b -> backspace)."""

    def test_quote_and_backslash_keys_round_trip(self, tmp_path: object) -> None:
        cfg = ProbeGateConfig(api_key='sk"quote', base_url="http://back\\slash")
        path = tmp_path / ".probegate.toml"
        path.write_text(_toml_dump(cfg), encoding="utf-8")
        loaded = _load_config(path)
        assert loaded is not None
        assert loaded.api_key == 'sk"quote'
        assert loaded.base_url == "http://back\\slash"

    def test_control_character_key_is_escaped(self, tmp_path: object) -> None:
        cfg = ProbeGateConfig(api_key="key\bboundary")
        path = tmp_path / ".probegate.toml"
        path.write_text(_toml_dump(cfg), encoding="utf-8")
        loaded = _load_config(path)
        assert loaded is not None
        assert loaded.api_key == "key\bboundary"

    def test_init_then_demo_loads_hostile_key(self, tmp_path: object, monkeypatch: object) -> None:
        # end-to-end: `probegate init --api-key 'sk"quote'` followed by
        # `probegate demo` must load the config, not die on a TOMLDecodeError.
        monkeypatch.chdir(tmp_path)
        assert main(["init", "--api-key", 'sk"quote']) == 0
        assert main(["demo"]) == 0


# --------------------------------------------------------------------------
# (h) TestProbe — async def test_* must be executed, not silently skipped
#     (v0.9.0 fix-test-probe-async-tests-skip)
# --------------------------------------------------------------------------

class TestTestProbeAsyncContract:
    """test_names collected ast.FunctionDef only, so an `async def test_*`
    was never run and an async-test span passed vacuously with the misleading
    evidence "no test_* functions in span — nothing to verify"."""

    def test_failing_async_test_fails_the_probe(self) -> None:
        code = "async def test_payload():\n    assert False, 'violating span'\n"
        result = TestProbe().run(_span(code))
        assert result.passed is False
        assert "test_payload" in result.evidence
        assert "no test_* functions" not in result.evidence

    def test_passing_async_test_passes_and_is_counted(self) -> None:
        code = "async def test_ok():\n    assert 1 + 1 == 2\n"
        result = TestProbe().run(_span(code))
        assert result.passed is True
        assert "1 test_* function(s) passed" in result.evidence

    def test_mixed_sync_and_async_both_run(self) -> None:
        code = (
            "def test_sync():\n"
            "    assert True\n"
            "async def test_async():\n"
            "    assert True\n"
        )
        result = TestProbe().run(_span(code))
        assert result.passed is True
        assert "2 test_* function(s) passed" in result.evidence

    def test_async_tests_inside_running_loop_reported_honestly(self) -> None:
        # the guard_async context: probe.run() executes inside a running loop,
        # where asyncio.run would crash — the tests are skipped with honest
        # evidence naming them, never the false "nothing to verify" pass.
        code = "async def test_loop():\n    assert True\n"

        async def call() -> object:
            return TestProbe().run(_span(code))

        result = asyncio.run(call())
        assert "no test_* functions" not in result.evidence
        assert "test_loop" in result.evidence
        assert "not executed" in result.evidence
        assert result.passed is True


# --------------------------------------------------------------------------
# (i) CLI audit-log wiring — demo/gate decisions + interactive operator
#     actions are persisted (v0.9.0 feat-cli-audit-log-wiring)
# --------------------------------------------------------------------------

class TestCliAuditWiringContract:
    """The v0.3.0 audit-log feature wired only the web endpoints; the CLI —
    the default handoff surface and the documented happy path — logged
    nothing. The CLI now persists each GateDecision and each interactive
    approve/reject, mirroring /api/guard + /api/approve + /api/reject."""

    def _log(self, tmp_path: object) -> LocalAuditLog:
        return LocalAuditLog(path=tmp_path / ".probegate" / "audit.jsonl")

    def test_cmd_gate_persists_decisions(self, tmp_path: object, monkeypatch: object) -> None:
        monkeypatch.chdir(tmp_path)
        assert main(["gate", "--demo"]) == 0
        records = [r for r in self._log(tmp_path).read() if r.get("type") == "decision"]
        assert len(records) == 5

    def test_cmd_demo_interactive_reject_persists_action(self, tmp_path: object, monkeypatch: object) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("probegate.cli.Confirm.ask", lambda *a, **k: False)
        assert main(["demo", "--interactive"]) == 0
        records = self._log(tmp_path).read()
        decisions = [r for r in records if r.get("type") == "decision"]
        actions = [r for r in records if r.get("type") == "action"]
        assert len(decisions) == 5
        # spans 4 and 5 both hand off with the compile probe at tau 0.5
        assert len(actions) == 2
        assert {a["span_id"] for a in actions} == {"span-4", "span-5"}
        assert all(a["action"] == "rejected" for a in actions)
        assert all(a["rule"] == "rewind" for a in actions)
