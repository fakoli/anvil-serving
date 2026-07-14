![anvil-serving - the quality-gated local-model router for coding agents](assets/banner.png)

# anvil-serving

> **The quality-gated local-model router for coding agents.**
>
> *Run local where it is measured safe. Verify risky local output. Keep cloud explicit.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/fakoli/anvil-serving/blob/main/LICENSE)
[![Source Version](https://img.shields.io/badge/source-0.13.2-blue.svg)](https://github.com/fakoli/anvil-serving/blob/main/CHANGELOG.md)
[![Docs](https://img.shields.io/badge/docs-fakoli.github.io%2Fanvil--serving-blue.svg)](https://fakoli.github.io/anvil-serving/)

anvil-serving sits between coding agents and local/cloud model tiers. It speaks Anthropic Messages
and OpenAI Chat Completions, routes each request by workload intent, checks risky local output
before returning it, and keeps metered cloud usage explicit. A token proxy moves requests;
anvil-serving answers the question a proxy cannot: **is this local model trusted for this kind of
work?**

For OpenClaw and remote operations, it also exposes explicit MCP/controller tools for status,
voice lifecycle, preflight, benchmark, OpenClaw sync, and promotion evidence. The router stays the
data plane; the control plane handles operations across same-host, private, and tailnet-reachable
device topologies.

## Choose Your Path

**Evaluating anvil-serving?**

1. [Getting started](GETTING-STARTED.md) — the no-GPU smoke test proves the protocol front door
   in two commands.
2. [Architecture](ARCHITECTURE.md) — the concise system overview with diagrams.
3. [Quality-gated router](QUALITY-GATED-ROUTER.md) — the full design reference and the evidence
   behind it.

**Operating a deployment?**

1. [Getting started](GETTING-STARTED.md) (Track B) — route real local tiers.
2. [Configuration reference](CONFIGURATION.md) and [CLI reference](CLI.md) — every knob and a
   task-oriented map of every verb, including [model recipes](cli/models.md#recipes).
3. [Operator playbooks](OPERATOR-PLAYBOOKS.md) — MCP/controller workflows for day-to-day
   operations.
4. [Device topologies](DEVICE-TOPOLOGIES.md) and [Troubleshooting](TROUBLESHOOTING.md) — grow the
   deployment and fix it when it misbehaves.
5. [`examples/fakoli-dark/`](https://github.com/fakoli/anvil-serving/tree/main/examples/fakoli-dark/)
   — a fully worked two-GPU reference instance.

**Comparing measured model results?**

1. [Benchmark results](BENCHMARKS.md) — the current public summary, tested configurations, and
   recommendation status.
2. [Findings](findings/README.md) — dated reports and machine-readable evidence behind each result.
3. [External benchmarks](EXTERNAL-BENCHMARKS.md) — advisory external throughput priors and how to
   compare them with a local run.

**Contributing?**

1. [CONTRIBUTING](https://github.com/fakoli/anvil-serving/blob/main/CONTRIBUTING.md) — setup, the
   hard rules, the module map, and extension recipes.
2. [Architecture](ARCHITECTURE.md) — how the pipeline fits together.
3. [ADRs](adr/README.md) — the *why* behind the design decisions.

## The Product Promise

| Question | anvil-serving answer |
|----------|----------------------|
| Can this run local? | Only if the quality profile says the tier is trusted for that work class. |
| What if local output is risky? | Buffer and structurally verify before committing the response. |
| What if local cannot serve it? | Exhaust cleanly or use an explicitly configured cloud tier. |
| Will this lock me into one harness? | No. The front door is protocol-standard; OpenClaw is the reference integration. |
| How do agents operate it? | Through explicit MCP/controller tools, not raw SSH as the product contract. |

## Operating Defaults

- Default config is local-only and contains no cloud API key.
- Metered cloud requires an explicit cloud tier plus `[router].metered_cloud`.
- Local URLs use `127.0.0.1`.
- Credentials are referenced by env-var name only.
- Token auth is required before exposing the router beyond loopback.
- OpenClaw's native fallback is not a reliable safety net for plugin-pinned local-preferred classes;
  use `ANVIL_CLOUD_CLASSES` or anvil-serving's opt-in cloud tier for at-risk work.
- The MCP/controller control plane is for explicit operations; the router remains the model data plane.

## Documentation Map

| Read this | When you need |
|-----------|---------------|
| [Getting started](GETTING-STARTED.md) | Evaluate the front door, then route real local tiers. |
| [Architecture](ARCHITECTURE.md) | The concise system overview: request path, tier ladder, quality profile, deployment shapes. |
| [Configuration reference](CONFIGURATION.md) | Every `[server]`/`[router]`/tier/mode key, env vars, and the shipped example configs. |
| [CLI reference](CLI.md) | Navigate every verb by router, serves, models and recipes, evaluation, host, control-plane, or voice workflow. |
| [Troubleshooting](TROUBLESHOOTING.md) | Symptom-first fixes: 503 exhaustion, preflight failures, empty responses, auth. |
| [Quality-gated router](QUALITY-GATED-ROUTER.md) | The full design reference: intents, routing, verification, and fallback. |
| [Terminology](TERMINOLOGY.md) | Product naming, user-facing terms, and technical definitions. |
| [Operator playbooks](OPERATOR-PLAYBOOKS.md) | Run MCP/controller workflows. |
| [Operator skills and sub-agents](OPERATOR-SKILLS-AND-SUBAGENTS.md) | Map verbs to MCP/skills and run small-model workflow slices safely. |
| [Device topologies](DEVICE-TOPOLOGIES.md) | Spread gateway, voice, router, and serve roles across hosts over private connectivity. |
| [Model settings](MODEL-SETTINGS-EXAMPLE.md) | Tune thinking/sampling behavior for a served model. |
| [Serves & eval](SERVES-AND-EVAL.md) | Manage model serves and run evals. |
| [ComfyUI migration runbook](COMFYUI-MIGRATION-RUNBOOK.md) | Migrate the ComfyUI model library into its named volume and run the on-demand tenant. |
| [Tailnet endpoint runbook](TAILNET-ENDPOINT-RUNBOOK.md) | Reach the one front door over Tailscale MagicDNS: the single-endpoint decision, binding, token auth, optional HTTPS via `tailscale serve`, and ComfyUI path routing. |
| [Voice pipeline](VOICE.md) | Run native voice commands, profile switches, private audio bridges, multi-device audio/LLM topology, Realtime server, and benchmarks. |
| [Benchmark results](BENCHMARKS.md) | Compare current measured model and end-to-end results with their exact configurations and caveats. |
| [External benchmarks](EXTERNAL-BENCHMARKS.md) | Import and compare advisory benchmark data. |
| [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) | Use the reference gateway integration. |
| [Hugging Face speech-to-speech](https://github.com/fakoli/anvil-serving/tree/main/examples/huggingface-speech-to-speech/) | Run Realtime audio with anvil-routed LLM turns. |
| [ADRs](adr/README.md) | Read why major decisions were made. |
| [Findings](findings/README.md) | Dated evidence snapshots behind the decisions. |
| [Changelog](changelog.md) | Track release history. |
