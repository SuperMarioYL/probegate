**English** | [简体中文](README.md)

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="1000" alt="Produce proceed, abstain or handoff for each span, retaining probe evidence and the decision rationale.">
</picture>

**Produce proceed, abstain or handoff for each span, retaining probe evidence and the decision rationale.**

`v0.9.0` · `Python 3.10+` · [MIT](LICENSE)

[Website](https://probegate.lei6393.com) · [Demo record](docs/demo-results.json)

## Why use it

A step may be syntactically wrong yet confident, or merely express uncertainty. ProbeGate separates uncertainty from machine-checkable evidence and makes the handling policy explicit so an agent caller can continue, flag or request human review.

## Architecture

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="1000" alt="guard/guard_async reads uncertainty or fetches it from an endpoint, runs the selected Probe and constructs GateDecision. handoff requires uncertainty > threshold and a failed probe. One signal produces abstain; neither produces proceed. Returning a decision does not itself stop the caller’s agent.">
</picture>

guard/guard_async reads uncertainty or fetches it from an endpoint, runs the selected Probe and constructs GateDecision. handoff requires uncertainty > threshold and a failed probe. One signal produces abstain; neither produces proceed. Returning a decision does not itself stop the caller’s agent.

Source entry points: [probegate/gate.py](probegate/gate.py) · [probegate/models.py](probegate/models.py) · [probegate/uncertainty.py](probegate/uncertainty.py) · [probegate/probes/compile_probe.py](probegate/probes/compile_probe.py) · [probegate/probes/test_probe.py](probegate/probes/test_probe.py) · [probegate/probes/lint_probe.py](probegate/probes/lint_probe.py) · [probegate/probes/schema_probe.py](probegate/probes/schema_probe.py) · [probegate/cli.py](probegate/cli.py)

## Install

The package supports Python 3.10+; these instructions use Python 3.12 and uv. The example loads no endpoint credentials and calls no model.

```bash
git clone https://github.com/SuperMarioYL/probegate.git
cd probegate
uv venv --python 3.12
uv pip install --python .venv/bin/python -e .
```

## Quickstart

Four synthetic spans explicitly supply uncertainty 0.1/0.9 and valid/invalid Python. The real compile probe exercises the AND policy; this is not a live-model calibration experiment.

```bash
.venv/bin/python examples/presentation-demo.py
```

Complete inputs and execution steps are included in the commands above and the [demo record](docs/demo-results.json).

## Usage

```bash
.venv/bin/probegate init
.venv/bin/probegate gate --demo --probe compile
.venv/bin/probegate ui --port 8000
```
A custom JSONL row can be `{"id":"s1","agent_step":1,"content":"answer = 42","uncertainty":0.1}`, consumed with `gate --fake-spans spans.jsonl`. Async agents should use `await gate.guard_async(span)`. The caller must explicitly act on handoff.

## Recorded demo

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="1000" alt="Four synthetic spans explicitly supply uncertainty 0.1/0.9 and valid/invalid Python. The real compile probe exercises the AND policy; this is not a live-model calibration experiment.">
</picture>

### Inspect all signal combinations

Only high uncertainty combined with a syntax failure produces handoff.

```text
$ .venv/bin/python examples/presentation-demo.py
[
  {
    "case": "clear",
    "uncertainty": 0.1,
    "probe_passed": true,
    "rule": "proceed"
  },
  {
    "case": "uncertain",
    "uncertainty": 0.9,
    "probe_passed": true,
    "rule": "abstain"
  },
  {
    "case": "syntax_only",
    "uncertainty": 0.1,
    "probe_passed": false,
    "rule": "abstain"
  },
  {
    "case": "both",
    "uncertainty": 0.9,
    "probe_passed": false,
    "rule": "handoff"
  }
]
```

## Capabilities and integration

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="1000" alt="CLI and Python API share decision models, and the UI can display per-span decisions. compile byte-compiles Python without running it; other probes have their own input contracts to inspect before integration.">
</picture>

CLI and Python API share decision models, and the UI can display per-span decisions. compile byte-compiles Python without running it; other probes have their own input contracts to inspect before integration.



## Configuration

`.probegate.toml` configures uncertainty_threshold, probe, model_target, handoff_mode, api_key and base_url. Defaults are threshold=0.5 and probe=compile. With a key and URL the adapter can fetch a logprob signal; failures fall back to self-report with a degradation marker in the rationale.

## Roadmap and scope

Four probes, async evaluation, endpoint fetching and local UI are implemented. Team identity, hosted relays and broader calibration experiments remain future directions.

- A failed probe with low uncertainty only abstains, without automatic handoff; this AND policy does not catch every error.
- Passing syntax checks does not establish program correctness; logprobs or self-report do not establish calibrated correctness probabilities.

![Terminal recording](assets/demo.gif) · [Recording script](docs/demo.tape)

## License

[MIT](LICENSE)
