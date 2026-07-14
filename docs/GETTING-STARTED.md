# Getting Started

This guide has two tracks:

- **No-GPU evaluator smoke test:** prove the protocol front door without a model server.
- **Real local-tier run:** route requests through local OpenAI-compatible serves.

Use `127.0.0.1` in local URLs.

## Prerequisites

- **Python >= 3.11** — the runtime is standard-library only; there are no required dependencies
  to install beyond the package itself.
- **No GPU and no Docker** are needed for Track A.
- **For Track B:** OpenAI-compatible model serves (SGLang or vLLM), typically run with Docker and
  Compose v2 on a GPU host. `anvil-serving doctor` checks a machine for exactly these
  requirements.

## Install

For the current `main` documentation and MCP/controller command surface, install from this clone:

```bash
pip install -e .
```

For released features only, you can install the latest published package:

```bash
pip install anvil-serving
```

Published packages can lag `main`, so use the editable clone install when a command documented here
is missing from the package.

Confirm the CLI is available:

```bash
anvil-serving --help
```

## Track A: No-GPU Smoke Test

The module entry point starts the router front door with a deterministic echo backend. It exercises
the protocol surface without loading a model and without touching cloud credentials.

In one terminal:

```bash
python -m anvil_serving.router
```

If port `8000` is already in use, pass `--port <free-port>` and use that port in the URLs below.

In another terminal, list the intent presets advertised as models:

```bash
curl -s http://127.0.0.1:8000/v1/models
```

Send an OpenAI-compatible chat request:

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"chat","messages":[{"role":"user","content":"hello from anvil-serving"}]}'
```

Expected result: a JSON response whose content echoes the user message. This proves the front door,
model discovery, request parsing, and response rendering. It does not prove local model quality or
the tier routing policy.

## Track B: Route Real Local Tiers

`configs/example.toml` is local-only. It expects compatible OpenAI-style model serves at:

| Tier | URL | Purpose |
|------|-----|---------|
| `fast-local` | `http://127.0.0.1:30001/v1` | Low-latency local work. |
| `heavy-local` | `http://127.0.0.1:30000/v1` | Higher-capacity local work. |

**Where do these serves come from?** anvil-serving manages local model serves as Docker Compose
services: declare them in a manifest, then run `anvil-serving serves up` (see
[Serves & eval](SERVES-AND-EVAL.md)). `anvil-serving serves render` renders a tuned compose file for a
given GPU and model, and `configs/serve-recipes.toml` in the repository carries known-good serve
recipes. The model names below (`gpt-oss-20b`, `qwen35-awq-local`) are not magic — they are the
`model` values the two tiers in `configs/example.toml` declare; if your serves run different
models, change the config's tier `model` fields (and these commands) to match.

**Fastest path for a full machine — `init --home`.** Rather than hand-writing the manifests and
compose files, `anvil-serving init --home` scaffolds the whole operational set (all `serves*.toml`
manifests with their group tags, the compose files, `operator-topology.toml`, `.env.example`, and
the ADR-0019 tailnet `edge.toml`) into `~/.anvil-serving/` — the default search dir for `serves`
and `router`. A fresh machine then runs a whole tier group with zero hand-assembly:

```bash
anvil-serving init --home            # scaffold into ~/.anvil-serving (or --out-dir DIR)
cp ~/.anvil-serving/.env.example ~/.anvil-serving/.env   # then fill host values + secrets
anvil-serving serves groups          # voice / fast-only / heavy-only / embedding / llm-stack / comfy
anvil-serving serves up --group voice
```

Host-specific values (GPU UUIDs, tailnet address) land as clearly-marked placeholders you edit
before bring-up; secrets are never written (only `.env.example`). Existing operator files are
backed up (`.anvil.bak.N`), never clobbered. See [`init --home`](CLI.md#init) for the full set.

Before starting the router, stand up those serves and validate each endpoint:

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model qwen35-awq-local
```

Then start the router:

```bash
anvil-serving router run --config configs/example.toml
```

Point a harness at the router:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
export ANTHROPIC_MODEL="planning"

export OPENAI_API_BASE="http://127.0.0.1:8000/v1"
```

Use preset tokens such as `planning`, `quick-edit`, `review`, `chat`, `chat-fast`, and `long-context` as the
model id.

## Auth Before Exposure

Loopback development does not require built-in auth. Before exposing the router beyond loopback,
configure auth by env-var name:

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
```

Then set the token in the environment and send it as either `Authorization: Bearer <token>` or
`x-api-key: <token>`.

Do not put cloud API keys, router tokens, or other secrets directly in config files.

## If Something Fails

The [Troubleshooting](TROUBLESHOOTING.md) guide is symptom-first; the entries you are most likely
to need on a first run:

- Port `8000` already in use → pass `--port <free-port>`.
- `preflight` fails → the serve is not up, or the `--model` name does not match the serve's
  served model name.
- The router answers `503` → that is the quality gate exhausting cleanly, not a crash; see the
  troubleshooting entry before changing anything.
- Requests hang ~20s on Windows → a `localhost` URL sneaked in; use `127.0.0.1`.

## Next Steps

- Read [Architecture](ARCHITECTURE.md) for the concise system overview, then
  [Quality-gated router](QUALITY-GATED-ROUTER.md) for the full design rationale.
- Read the [Configuration reference](CONFIGURATION.md) to adapt `configs/example.toml` to your
  serves, and the [CLI reference](CLI.md) for the full command surface.
- Read [Device topologies](DEVICE-TOPOLOGIES.md) before spreading gateway, voice, router, or serve roles across more devices.
- Read [Model settings](MODEL-SETTINGS-EXAMPLE.md) before serving thinking-by-default models.
- Read [Serves & eval](SERVES-AND-EVAL.md) to manage Docker Compose model serves.
- Read [Voice pipeline](VOICE.md) to run STT/TTS lifecycle, the Realtime voice server, and model-free Mini gateway validation.
- Read [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) for the reference gateway setup.
