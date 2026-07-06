# Getting Started

This guide has two tracks:

- **No-GPU evaluator smoke test:** prove the protocol front door without a model server.
- **Real local-tier run:** route requests through local OpenAI-compatible serves.

Use `127.0.0.1` in local URLs.

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

Before starting the router, stand up those serves and validate each endpoint:

```bash
anvil-serving preflight --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b
anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model qwen35-awq-local
```

Then start the router:

```bash
anvil-serving serve --config configs/example.toml
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

## Next Steps

- Read [Product architecture](QUALITY-GATED-ROUTER.md) for the routing model.
- Read [Device topologies](DEVICE-TOPOLOGIES.md) before spreading gateway, voice, router, or serve roles across more devices.
- Read [Model settings](MODEL-SETTINGS-EXAMPLE.md) before serving thinking-by-default models.
- Read [Serves & eval](SERVES-AND-EVAL.md) to manage Docker Compose model serves.
- Read [Voice pipeline](VOICE.md) to run STT/TTS lifecycle, the Realtime voice server, and Mini validation.
- Read [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) for the reference gateway setup.
