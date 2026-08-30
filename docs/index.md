![Anvil Serving - explicit local AI capabilities](assets/banner.png)

# anvil-serving

> **Operate, qualify, and expose local AI capabilities through explicit contracts.**

Anvil Serving is one product for Model Serving, the Capability Gateway,
Evaluation & Evidence, Anvil Voice, Anvil Media, and Control Plane & Fleet
operations. Each family has an explicit authority boundary and ordered CLI
journey. They share one package and release line.

Within that umbrella, the gateway is a capability meta-router: each configured
alias maps to exactly one local tier. The selected inference service may own
its mutable served-model metadata, while the router owns the alias, tier
mapping, policy, and protocol boundary. It is not an intent classifier or an
automatic model selector.

Models are interchangeable subjects of evaluation here. The docs below describe capabilities;
the [evidence layer](benchmarks/index.md) records which models currently occupy which tier and
what was measured to put them there.

## Product families

| Family | Start here | Commands |
| --- | --- | --- |
| **Model Serving** — artifacts, recipes, lifecycle, reservations, and guarded promotion | [Model lifecycle](MODEL-LIFECYCLE.md) | `init`, `models`, `serves` |
| **Capability Gateway** — exact aliases, auth, translation, readiness, admission, and streaming | [Capability meta-router](META-ROUTER.md) | `router` |
| **Evaluation & Evidence** — preflight, routed acceptance, and comparison-safe benchmarks | [Evaluation commands](cli/eval.md) | `eval` |
| **Anvil Voice** — STT/TTS, realtime proxy, profiles, and voice qualification | [Voice pipeline](VOICE.md) | `voice` |
| **Anvil Media** — bounded named workflows, durable jobs, qualification, and artifacts | [Media commands](cli/media.md) | `media` |
| **Control Plane & Fleet** — topology, typed dispatch, host utilities, integrations, and fleet state | [Control Plane & Fleet](cli/control-plane.md) | `topology`, `controller`, `mcp`, `fleet`, `host` |

Run `anvil-serving product families` for the installed catalog or start with
[Product families and user journeys](PRODUCT-FAMILIES.md). Then use
[Getting started](GETTING-STARTED.md) for the first serving-and-gateway path.

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
| [RTX 5090](benchmarks/hardware/rtx-5090.md) | Image/video generation, Omni, vision, and STT/TTS measurements. |
| [Result archive](BENCHMARKS.md) | Chronological campaigns and historical comparisons. |
| [Findings](findings/README.md) | Dated evidence snapshots, immutable once published. |
| [Decisions (ADRs)](adr/README.md) | Why the architecture is shaped the way it is. |

## Reference

| Read this | When you need |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | System components and deployment shapes. |
| [Product families](PRODUCT-FAMILIES.md) | Umbrella story, boundaries, and ordered user journeys. |
| [Capability meta-router](META-ROUTER.md) | Product category, authority split, and non-goals. |
| [Configuration reference](CONFIGURATION.md) | Router, serve, and voice configuration. |
| [Command index](CLI.md) | Every command family and flag. |
| [Troubleshooting](TROUBLESHOOTING.md) | Diagnose aliases, serves, auth, and preflight. |
| [Terminology](TERMINOLOGY.md) | Tier, alias, serve, and evidence vocabulary. |
| [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) | Gateway provider contract. |
