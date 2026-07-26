![anvil-serving - local model serving and benchmarking](assets/banner.png)

# anvil-serving

> **Local model serving, benchmarks, and a thin capability gateway.**

anvil-serving manages local model serves, validates them with preflight checks, records
benchmark evidence, and exposes explicit model aliases through OpenAI- and
Anthropic-compatible endpoints. Each configured alias maps to one local tier; the router is a
proxy boundary, not an intent classifier or automatic model selector.

For OpenClaw and remote operations, the repository also provides explicit MCP/controller tools
for status, serving lifecycle, voice audio, preflight, benchmark runs, and provider sync.

## Start here

1. [Getting started](GETTING-STARTED.md) — bring up the protocol front door.
2. [Thin capability gateway](THIN-CAPABILITY-GATEWAY.md) — direct aliases, error behavior, and
   the request boundary.
3. [Configuration reference](CONFIGURATION.md) — configure local tiers and `[router.model_routes]`.
4. [Serves & eval](SERVES-AND-EVAL.md) — manage model serves and produce benchmark evidence.
5. [Operator playbooks](OPERATOR-PLAYBOOKS.md) — repeatable controller and CLI workflows.

## Operating defaults

- Model traffic uses configured direct aliases and one local target per alias.
- Local URLs use `127.0.0.1`; credentials are environment-variable references only.
- Token authentication is required before exposing the router beyond loopback.
- A benchmark or preflight result never changes a serve or alias binding automatically.
- Mini remains model-free in the reference OpenClaw voice topology; Dark owns the LLM and audio
  model serves.

## Documentation map

| Read this | When you need |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | System components and deployment shapes. |
| [Thin capability gateway](THIN-CAPABILITY-GATEWAY.md) | Direct alias behavior and API contract. |
| [Configuration reference](CONFIGURATION.md) | Router, serve, and voice configuration. |
| [CLI reference](CLI.md) | Command families and flags. |
| [Serves & eval](SERVES-AND-EVAL.md) | Serve lifecycle, preflight, and benchmarks. |
| [Voice pipeline](VOICE.md) | STT/TTS, realtime, and Mini-to-Dark topology. |
| [Operator playbooks](OPERATOR-PLAYBOOKS.md) | Controlled operations and evidence publication. |
| [Troubleshooting](TROUBLESHOOTING.md) | Diagnose aliases, serves, auth, and preflight. |
| [Benchmark results](BENCHMARKS.md) | Current measured results and caveats. |
| [Findings](findings/README.md) | Dated evidence snapshots. |
| [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) | Gateway provider contract. |
