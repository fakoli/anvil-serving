![anvil-serving - local model serving and benchmarking](assets/banner.png)

# anvil-serving

> **Run local models, prove they work, and serve them through a thin capability gateway.**

anvil-serving manages local model serves, qualifies them with preflight checks, records
benchmark evidence, and exposes explicit model aliases over OpenAI- and Anthropic-compatible
endpoints. Each configured alias maps to exactly one local tier. The router is a proxy
boundary — not an intent classifier, not an automatic model selector.

Models are interchangeable subjects of evaluation here. The docs below describe capabilities;
the [evidence layer](benchmarks/index.md) records which models currently occupy which tier and
what was measured to put them there.

## What you can do today

| Capability | Start here | Commands |
| --- | --- | --- |
| **Serve models** — catalog, artifacts, recipes, and the serve lifecycle | [Catalog, artifacts & recipes](MODEL-LIFECYCLE.md) | `models`, `serves` |
| **Promote and roll back** — the guarded transaction that changes what callers get | [Promote and roll back](MODEL-PROMOTION.md) | `serves promote`, `router quiesce/drain/readmit` |
| **Qualify & benchmark** — prove an endpoint is a capability, not just reachable | [Evaluation & benchmarks](cli/eval.md) | `eval` |
| **Route through the gateway** — direct aliases, auth, streaming, admission | [Thin capability gateway](THIN-CAPABILITY-GATEWAY.md) | `router` |
| **Embeddings & reranking** — dedicated purpose models routed by exact model name | [Embeddings & reranking](PURPOSE-MODELS.md) | `router` |
| **Voice & audio** — STT/TTS serves, realtime bridge, audio routes | [Voice pipeline](VOICE.md) | `voice` |
| **Operate the host** — GPU budget, WSL/Docker repair, tailnet edge, observability | [Operator playbooks](OPERATOR-PLAYBOOKS.md) | `host`, `edge`, `collectors`, `dashboard`, `topology` |
| **Integrate a harness** — MCP/controller tools and provider config for agents | [Agent workbench](WORKBENCH.md) | `workbench`, `harness`, `mcp`, `controller` |

New to the project? Start with [Getting started](GETTING-STARTED.md) to bring up the protocol
front door, then [Configuration reference](CONFIGURATION.md) to declare local tiers and
`[router.model_routes]`.

## Operating defaults

- Model traffic uses configured direct aliases, one local target per alias.
- Local URLs use `127.0.0.1`; credentials are environment-variable references only.
- Token authentication is required before exposing the router beyond loopback.
- A benchmark or preflight result never changes a serve or alias binding automatically.
- Readiness proves an endpoint accepts traffic. Only `eval preflight` and benchmark artifacts
  establish that it is a qualified capability.

## Evidence

Current model occupants, hardware measurements, and the reasoning behind every promotion live
in a separate, dated layer — deliberately kept apart from the capability docs above, so that
reference pages stay true as models change.

| Read this | When you need |
| --- | --- |
| [Model comparison table](benchmarks/comparison.md) | **Every measured configuration in one table** — TTFT, throughput, reasoning, and recipe links. |
| [Benchmarks overview](benchmarks/index.md) | Hardware-first current decisions and evidence labels. |
| [Model dossiers](benchmarks/models/index.md) | Per-model status, recipe, and decision boundary. |
| [RTX PRO 6000](benchmarks/hardware/rtx-pro-6000.md) | Primary LLM measurements and rollback chain. |
| [RTX 5090](benchmarks/hardware/rtx-5090.md) | Omni, vision, and STT/TTS measurements. |
| [Result archive](BENCHMARKS.md) | Chronological campaigns and historical comparisons. |
| [Findings](findings/README.md) | Dated evidence snapshots, immutable once published. |
| [Decisions (ADRs)](adr/README.md) | Why the architecture is shaped the way it is. |

## Reference

| Read this | When you need |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | System components and deployment shapes. |
| [Configuration reference](CONFIGURATION.md) | Router, serve, and voice configuration. |
| [Command index](CLI.md) | Every command family and flag. |
| [Troubleshooting](TROUBLESHOOTING.md) | Diagnose aliases, serves, auth, and preflight. |
| [Terminology](TERMINOLOGY.md) | Tier, alias, serve, and evidence vocabulary. |
| [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) | Gateway provider contract. |
