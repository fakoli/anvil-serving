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

Before `anvil-serving init` on a real host, choose a private operator root. For
example, point `ANVIL_SERVING_HOME` at the `operator-home/` directory of a
private companion repository. This keeps real topology and active assignments
out of the public product checkout. See
[Public product and private operator state](OPERATOR-PRIVACY.md).

Confirm the CLI is available:

```bash
anvil-serving --help
```

## Start Real Local Tiers

`configs/example.toml` is a public loopback template. It expects compatible
OpenAI-style model serves at:

| Tier | URL | Purpose |
|------|-----|---------|
| `omni-local` | `http://127.0.0.1:30003/v1` | Auxiliary text, general vision, and OCR. |
| `primary-local` | `http://127.0.0.1:30002/v1` | Higher-capacity local work. |

**Where do these serves come from?** anvil-serving manages local model serves as Docker Compose
services: declare them in a manifest, then run `anvil-serving serves up` (see
[Operator playbooks](OPERATOR-PLAYBOOKS.md)). `anvil-serving serves render` renders a tuned compose file for a
given GPU and model, and `configs/serve-recipes.toml` in the repository carries known-good serve
recipes. The model names below (`gpt-oss-20b`, `qwen35-awq-local`) are not magic — they are the
`model` values the two tiers in `configs/example.toml` declare; if your serves run different
models, change the config's tier `model` fields (and these commands) to match.

For a single-model endpoint whose model or context changes independently, use
`metadata_source = "upstream"` and omit duplicated `model` and
`context_limit` values. The capability alias still maps to exactly one tier;
only the selected serve's effective metadata is refreshed. See
[Capability meta-router](META-ROUTER.md).

**Fastest path for a full machine — `anvil-serving init`.** Rather than hand-writing the manifests
and compose files, bare `anvil-serving init` scaffolds the whole operational set (all `serves*.toml`
manifests with their group tags, the compose files, `operator-topology.toml`, `.env.example`, and
the ADR-0019 tailnet `edge.toml`) into `~/.anvil-serving/` (or the directory
selected by `ANVIL_SERVING_HOME`). The set ships inside the installed package, so this works from a normal
`pip`/`uv tool install`, not just a source checkout. A fresh machine then runs a whole tier group
with zero hand-assembly:

```bash
anvil-serving init                   # scaffold into ~/.anvil-serving (or --out-dir DIR)
cp ~/.anvil-serving/.env.example ~/.anvil-serving/.env   # then fill host values + secrets
anvil-serving serves groups          # omni-stack / omni-voice-stack / auxiliary-stack / primary-only / llm-stack / voice / comfy
anvil-serving serves up --group omni-voice-stack --dry-run
anvil-serving serves up --group omni-voice-stack --confirm
anvil-serving serves up --group voice --dry-run
anvil-serving serves up --group voice --confirm
anvil-serving router run             # uses ~/.anvil-serving/router.toml
```

`init` detects stable NVIDIA GPU UUIDs and total memory, assigns the
highest-VRAM card to Primary and the lowest-VRAM card to Auxiliary, and resolves this
node's Tailscale IPv4 address. It writes those discovered values into
`operator-topology.toml` when it can identify the current node in the reference
topology. Equal capacities are resolved by runtime index.
The generated workload mapping puts the primary LLM on Primary. Auxiliary can
run the exclusive 30B Omni stack, or the smaller Omni model together with
dedicated STT/TTS through `omni-voice-stack`. Embeddings/reranking and ComfyUI
remain optional separate stacks.
Values it cannot detect remain clearly marked
placeholders. Secrets are never written (only `.env.example`). Existing
operator files are backed up (`.anvil.bak.N`) only when their generated content
differs; identical files are left in place without another backup. Use
`--primary-gpu-uuid`, `--auxiliary-gpu-uuid`, or `--tailnet-ip` to override discovery,
or `--no-detect-host` to keep all host placeholders. Topology-aware commands
default to `$ANVIL_SERVING_HOME/operator-topology.toml` after target resolution
is requested; explicit `--topology`, `--config`, and `--manifest` paths always
win. Set `ANVIL_SERVING_HOME` to use an alternate machine-level config
directory. For a single-model quick bring-up into the CWD instead, use
`anvil-serving init --single-model`. See [`init`](cli/host.md#init) for the full
set.

Before starting the router, stand up those serves and validate each endpoint. `--model` is the
serve's `--served-model-name`, so substitute whatever your manifest declares:

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:<port>/v1 --model <served-model>
```

For the two-tier `configs/example.toml` shape that means one preflight per tier endpoint, each
naming that tier's own `model` value.

Then start the router:

```bash
anvil-serving router run --config configs/example.toml
```

Point a harness at the router:

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000"
export ANTHROPIC_MODEL="llm.primary"

export OPENAI_API_BASE="http://127.0.0.1:8000/v1"
```

Use `llm.primary` for the primary LLM. The `llm.voice`, `vision.general`, and
`vision.ocr` aliases all select the one qualified Omni tier.
The smaller `omni-small` serve is intentionally not routed by default; switching
those aliases remains a human-gated promotion after model-quality review.

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
- The router answers `503` → the alias is configured but its local tier cannot serve right now.
  That is the gateway refusing cleanly rather than substituting another model; see the
  troubleshooting entry before changing anything.
- Requests hang ~20s on Windows → a `localhost` URL sneaked in; use `127.0.0.1`.

## Next Steps

- Read [Capability meta-router](META-ROUTER.md) for the product and authority
  model, [Architecture](ARCHITECTURE.md) for the system overview, then the
  [meta-router request path](THIN-CAPABILITY-GATEWAY.md) for runtime details.
- Read the [Configuration reference](CONFIGURATION.md) to adapt `configs/example.toml` to your
  serves, and the [CLI reference](CLI.md) for the full command surface.
- Read [Public product and private operator state](OPERATOR-PRIVACY.md) before
  recording real topology or deployment state.
- Read [Device topologies](DEVICE-TOPOLOGIES.md) before spreading gateway, voice, router, or serve roles across more devices.
- Read [Model settings](MODEL-SETTINGS-EXAMPLE.md) before serving thinking-by-default models.
- Read [Operator playbooks](OPERATOR-PLAYBOOKS.md) to manage Docker Compose model serves.
- Read [Voice pipeline](VOICE.md) to run STT/TTS lifecycle, the Realtime voice server, and model-free Mini gateway validation.
- Read [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) for the reference gateway setup.
