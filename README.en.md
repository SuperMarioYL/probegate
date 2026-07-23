**English** | [简体中文](./README.md)

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
    <img src="./assets/hero-light.svg" width="880" alt="ProbeGate — per-span probe-validation gate for 国产模型 agents">
  </picture>
</p>

<p align="center"><sub>Pairs a model's self-reported uncertainty with a machine-checkable probe and routes to a human only when both fire — the per-span fallback gate for 国产模型 autonomous agents.</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="license"></a>
  <a href="https://github.com/SuperMarioYL/probegate/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/probegate?style=flat-square&label=release" alt="release"></a>
  <a href="https://github.com/SuperMarioYL/probegate/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/probegate/ci.yml?branch=main&label=CI&style=flat-square" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-%E2%89%A53.10-blue?style=flat-square" alt="python">
  <img src="https://img.shields.io/badge/Autohand_Code-probe-orange?style=flat-square" alt="Autohand Code">
  <img src="https://img.shields.io/badge/Show_HN-launch-yellow?style=flat-square" alt="Show HN">
</p>

**In one breath**: your 国产模型 agent barrels through low-confidence steps — ProbeGate ANDs a compile/test/lint/schema probe with the model's self-reported uncertainty and halts the span for a human only when both signals fire; everything else proceeds.

ProbeGate is the Autohand Code abstention gate built on the machine-checkable probe layer [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills) already markets as "stack validation" — but wired into the live agent loop, not left as a composable block. It rides the same calibration narrative as [cactus-compute/cactus-hybrid](https://github.com/cactus-compute/cactus-hybrid)'s Show HN "we taught Gemma 4 to know when it's wrong" thread, then refuses to trust that black-box self-knowledge alone: the AND with a real probe is what makes the signal actionable on the 国产模型 serving tier where calibration lags frontier closed models hardest.

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Architecture</h2>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
    <img src="./assets/atlas-light.svg" width="880" alt="ProbeGate architecture: Agent loop → interceptor → (UncertaintyAdapter ∧ Probe Registry) → GateDecision → human fallback">
  </picture>
</p>

The load-bearing invariant:

```
handoff ⟺ uncertainty > τ  AND  not probe.passed
```

A single signal is insufficient by design — that is the structural split from the adjacent incumbents: [cactus-compute/cactus-hybrid](https://github.com/cactus-compute/cactus-hybrid) trusts model self-knowledge alone (a black-box-explanation illusion), and [sickn33/agentic-awesome-skills](https://github.com/sickn33/agentic-awesome-skills) owns the probe layer but never wires it into an in-flight gate. ProbeGate takes the 国产模型 serving tier — where self-reported confidence is least trustworthy — as the wedge, and ANDs it with a probe to catch exactly the spans where "confidence is lying high AND the compile/test/lint/schema check genuinely fails."

**Three gate states**: `proceed` (both clear, pass) · `abstain` (one signal, flag but don't hand off) · `handoff` (both fire, route to human).

### vs sickn31/agentic-awesome-skills

| axis | ProbeGate | agentic-awesome-skills |
|---|:---:|:---:|
| Machine-checkable probe layer (Autohand Code) | ✓ calls it | ✓ owns it |
| Dual-signal AND gate (uncertainty ∧ probe) | ✓ | — |
| Per-span in-flight UI gate | ✓ | — |
| Wired to 国产模型 logprob serving tier | ✓ | partial (model-agnostic) |
| Skill catalog breadth (1,987+ skills) | — | ✓ |

agentic-awesome-skills is genuinely better on the probe layer and catalog breadth — it is the *home* of the Autohand Code probe ProbeGate calls rather than reinvents. ProbeGate owns the dual-signal gate logic + per-span UI on top of it.

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Install + Quickstart</h2>

```bash
pip install probegate
probegate init                              # writes .probegate.toml (probe + threshold τ)
probegate demo --model deepseek-coder       # built-in 5-step agent, step 4 trips the gate
```

<details><summary>sample output</summary>

```
┏━━━━━━━━━━━━━━ ProbeGate demo ━━━━━━━━━━━━━━┓
┃ model=deepseek-coder  probe=compile  tau=0.5┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
╭─ span span-1 ───────────────────────────╮
│ uncertainty 0.08
│ probe     compile → PASS
│ rule      proceed
╰─────────────────────────────────────────╯
...
╭─ span span-4 ───────────────────────────╮     ← deliberately wrong signature
│ uncertainty 0.82
│ probe     compile → FAIL
│ rule      handoff                          ← both signals → route to human
╰─────────────────────────────────────────╯
```

</details>

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Usage</h2>

Wrap your agent loop in a `ProbeGate`:

```python
from probegate import ProbeGate, Span

with ProbeGate(uncertainty_threshold=0.5, probe="compile") as g:
    for span in agent.run():              # 国产模型 emits one span per step
        decision = g.guard(span)          # (a) uncertainty ∧ (b) probe → 3 states
        if decision.rule == "handoff":    # only when BOTH fire
            if not ask_human(span):       # N → agent rewinds this span
                rewind(span)
```

Common subcommands:

| command | what it does |
|---|---|
| `probegate init` | write `.probegate.toml` (threshold τ / probe / model target) |
| `probegate demo --model deepseek-coder` | built-in 5-step agent, step 4 deliberately breaks a signature → handoff |
| `probegate gate --demo --probe compile` | run the gate over built-in demo spans, print per-span 3-state |
| `probegate gate --fake-spans demo.jsonl` | run the gate over your own jsonl span stream |
| `probegate ui --port 8000` | serve the local FastAPI web view — per-span cards + Approve/Reject |

jsonl span format (one per line):

```json
{"id": "s4", "agent_step": 4, "content": "def f(:\n    pass\n", "uncertainty": 0.82}
```

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

<p align="center"><img src="./assets/demo.gif" width="880" alt="ProbeGate CLI demo: built-in 5-step agent, step 4 low-confidence + compile fail → handoff gate"></p>

A 60s recording: a DeepSeek-Coder agent's step-4 low-confidence span trips the compile probe → ProbeGate pops a per-span human fallback gate; contrast with the un-gated cascade that barrels into step 5.

<h2><img src="https://api.iconify.design/tabler:adjustments.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Configuration</h2>

`.probegate.toml` (generated by `probegate init`):

| key | type | default | meaning |
|---|---|---|---|
| `uncertainty_threshold` | float | `0.5` | τ — a span above this **AND** a failing probe ⇒ handoff |
| `probe` | `compile\|test\|lint\|schema` | `compile` | machine-checkable probe; m1 defaults to compile |
| `model_target` | string | `deepseek-coder` | 国产模型 serving target (`deepseek-coder` / `qwen3-coder` / `glm-5`) |
| `handoff_mode` | `cli\|web` | `cli` | where the human gate surfaces (CLI prompt or local web view) |
| `api_key` | string? | `""` | 国产模型 API key (m3: logprob fetch) |
| `base_url` | string? | `""` | OpenAI-compatible base URL (m3: logprob fetch) |

<h2><img src="https://api.iconify.design/tabler:credit-card.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Pricing</h2>

| plan | price | covers |
|---|---|---|
| **ProbeGate OSS** | free / MIT | local dual-signal gate + all probes + CLI + local web view (shipped in v0.1) |
| **ProbeGate Team** | ¥499/seat/yr (¥49/seat/mo) | audit-log export · SSO/team accounts · hosted per-span gate relay (v0.2+ paid anchor) |

The commercial path targets Chinese SME teams wiring agents into production (support / analytics / reporting), where one committed wrong span has a customer-visible cost — a single 10-step rollback burning half a dev-day already pays for a year of seats. v0.1 ships the OSS core only; Team is the explicit v0.2+ paid anchor, not vaporware. Team trial: `probegate.dev/team`.

<h2><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Roadmap</h2>

- [x] **m1 — dual-signal AND gate**: `GateDecision` + uncertainty ∧ compile probe + fake span stream + CLI 3-state printing
- [ ] **m2 — per-span web view**: FastAPI renders the trajectory, one card per span (uncertainty / probe result / gate state / Approve-Reject), wired to m1's GateDecision
- [ ] **m3 — wire 国产模型 logprob**: UncertaintyAdapter against DeepSeek-Coder / Qwen3-Coder / GLM API + add test/lint/schema probes + 10-min comparison demo recording
- [ ] v0.2 — ProbeGate Team: audit-log export, SSO, hosted gate relay

<h2><img src="https://api.iconify.design/tabler:license.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> License + Contributing</h2>

MIT, see [LICENSE](./LICENSE). File issues / PRs at [github.com/SuperMarioYL/probegate/issues](https://github.com/SuperMarioYL/probegate/issues).

After pushing, set topics: `gh repo edit --add-topic agent --add-topic abstention --add-topic deepseek --add-topic qwen --add-topic glm`.

## Share this

```
ProbeGate — the Autohand Code abstention gate that halts 国产-model agents on wrong steps. We don't trust the model's confidence; we compile-check it first. https://github.com/SuperMarioYL/probegate
```

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
