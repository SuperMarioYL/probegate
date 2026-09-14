# Changelog

All notable changes to ProbeGate are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), one section per release.

## [0.8.0] — 2026-09-14

### Fixed

- **SchemaProbe CRLF false negative** (`fix-schema-probe-crlf-fence-false-negative`):
  the `probegate:schema` / `probegate:payload` fence regex required a literal
  `\n` after the fence label, so a span authored with Windows CRLF line
  endings never matched and the probe silently PASSED with "no
  `probegate:schema` block" — a schema-violating span escaped the human gate
  (uncertainty above tau AND a violating payload emitted `abstain` instead of
  `handoff`). The fence pattern now accepts `\r?\n`, so CRLF fences are
  validated exactly like LF ones.
- **Release-branch drift** (`fix-release-branch-drift`): the v0.7.0 release
  shipped from an unmerged `v0.7.0-dev` branch, so the default branch never
  received it — `main` still declared `0.5.0` and lacked the v0.7.0
  audit-log corrupt-line guard and the version-lockstep test. v0.8.0 re-lands
  the v0.7.0 fixes on `main` and ships from the default branch, so a
  default-branch install carries every shipped fix.

## [0.7.0] — 2026-08-31

### Fixed

- Version drift: the three version-reporting surfaces (`pyproject.toml`,
  `probegate.__version__`, FastAPI `app.version`) left stale at `0.5.0` by the
  v0.6.0 ship; bumped in lockstep and pinned by a regression test.
- `LocalAuditLog.read()` no longer raises `json.JSONDecodeError` on a
  truncated/corrupt last line (the state a process killed mid-write leaves);
  the corrupt line is skipped and the salvageable records are returned.

## [0.6.0] — 2026-08-18

### Fixed

- `fetch_logprob` wraps `resp.json()` and the URL build so a 200 with a
  non-JSON body or a malformed `base_url` degrades to the un-trusted
  self-report path instead of crashing the agent loop.
- A degraded fetch now surfaces a `[fetch degraded: <reason>; using un-trusted
  self-report]` marker in the `GateDecision` rationale.

## [0.5.0] — 2026-08-14

### Fixed

- The logprob-fetch path survives async agent loops (`guard_async` added; sync
  `guard()` degrades instead of raising `RuntimeError` inside a running event
  loop) and transient API failures (httpx timeout / connect / 5xx degrade to
  the non-fatal `read(span)` path).

## [0.4.0] — 2026-08-12

### Fixed

- The m3 logprob fetch is wired into the gate runtime: `--api-key` /
  `--base-url` (and `.probegate.toml` creds) actually drive a fetch through
  `guard()` instead of only populating the config object.

## [0.3.0] — 2026-08-08

### Added

- Local audit log: every `GateDecision` and operator approve/reject action is
  appended to `.probegate/audit.jsonl` (OSS-local, no export/SSO/relay).

### Fixed

- `.probegate.toml` written by `probegate init` is now loaded by `demo` /
  `gate` / `ui` (flag wins), and `--api-key` / `--base-url` flags exist.
- `SchemaProbe` validates the schema shape instead of crashing on array-form
  `types` or iterating a string-form `required`.
- `POST /api/guard` returns HTTP 422 (not an unhandled 500) on a bad probe
  name, out-of-range threshold, or bad model target.

## [0.2.0] — 2026-08-03

### Added

- Real logprob fetch from the DeepSeek-Coder / Qwen3-Coder / GLM
  OpenAI-compatible endpoint (`UncertaintyAdapter.fetch_logprob`), plus probe
  contract tests pinning every advertised check.

### Fixed

- `LintProbe` implements the advertised `print(...)` check.
- `SchemaProbe` surfaces unknown declared types instead of claiming "typed",
  and requires a `probegate:payload` block when a schema block is present
  (the old whole-span JSON fallback was unreachable as written).

## [0.1.0] — 2026-07-23

### Added

- Initial release: the dual-signal abstention gate (`ProbeGate.guard`), the
  compile/test/lint/schema probes, the CLI (`init` / `demo` / `gate` / `ui`),
  and the local FastAPI per-span web view.
