# Getting Started

Anvil Serving helps you operate, qualify, and expose local AI capabilities
through explicit, reviewable contracts. This guide follows the
serving-and-gateway path from installation to a capability alias routed to a
local endpoint. Use the installed product catalog below for Voice, Media,
evaluation, or fleet-specific journeys.

Use `127.0.0.1` in local URLs.

## Prerequisites

- **Python >= 3.11** — the runtime is standard-library only; there are no required dependencies
  to install beyond the package itself.
- **At least one OpenAI-compatible model serve** for the routed request. SGLang
  and vLLM are common options.
- **For a managed Docker-backed GPU serve:** Docker, Compose v2, and a supported
  GPU host. `anvil-serving doctor` checks these host prerequisites.
- **For the reference multi-device deployment:** Tailscale on every participating
  server, harness, operator, and mobile device. A single-host loopback deployment
  does not require it. Read [Private networking with Tailscale](TAILSCALE-NETWORKING.md)
  before publishing any service across devices.

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
anvil-serving product families
```

If your first goal is Voice, Media, evaluation, or fleet operations rather
than the serving-and-gateway path below, ask the installed catalog for the
ordered journey:

```bash
anvil-serving product journey anvil-voice
anvil-serving product journey anvil-media
anvil-serving product journey evaluation-evidence
anvil-serving product journey control-plane-fleet
```

The six-family boundary and cross-family handoffs are documented in
[Product families and user journeys](PRODUCT-FAMILIES.md).

## Choose the network shape

A single-host installation keeps every URL on `127.0.0.1`; no private overlay
is required. In the reference multi-device shape, services still bind to
`127.0.0.1` on the device that owns them. Tailscale Serve projects only the
reviewed gateway, controller, voice, or media path onto that device's private
MagicDNS name.

This preserves local defaults while adding user/device identity and
least-privilege reachability between a primary inference node, harness node,
voice/audio node, media/burst node, and approved mobile clients. Tailscale
grants are the network boundary; Anvil router/controller tokens remain the
application boundary. Follow [Private networking with Tailscale](TAILSCALE-NETWORKING.md)
for the role map and policy layers, then [One tailnet endpoint](TAILNET-ENDPOINT-RUNBOOK.md)
for the exact managed edge commands.

## Run local tiers

`configs/example.toml` is a public loopback template. It expects compatible
OpenAI-style model serves at:

| Tier | URL | Purpose |
|------|-----|---------|
| `omni-local` | `http://127.0.0.1:30003/v1` | Voice-adjacent text, general vision, and OCR. |
| `primary-local` | `http://127.0.0.1:30002/v1` | Higher-capacity local work. |

**Where do these serves come from?** Anvil Serving manages local model serves as
Docker Compose services: declare them in a manifest, then run
`anvil-serving serves up` (see [Operator playbooks](OPERATOR-PLAYBOOKS.md)).
`anvil-serving serves render` renders a tuned Compose file for a given GPU and
model, and `configs/serve-recipes.toml` carries recorded serve recipes. Each
tier's `model` value must exactly match the model name advertised by its
endpoint. Update those values before preflight if your serves advertise
different names.

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

`init` detects stable NVIDIA GPU UUIDs and total memory, assigns the two largest
distinct cards to Compute A and Compute B, and resolves this node's Tailscale
IPv4 address. It writes those discovered values into `operator-topology.toml`
when it can identify the current node in the reference topology. Equal-VRAM
cards use canonical UUID ordering rather than runtime index, so a reboot cannot
swap their stable roles. Workload capability remains independent of A/B
placement, and a one-GPU machine leaves the second role unresolved rather than
assigning both concurrent roles to one card. The generated manifests use those
stable roles for declared reservations; lifecycle and promotion remain managed
through `serves` commands and explicit groups.
Values it cannot detect remain clearly marked
placeholders. Secrets are never written (only `.env.example`). Existing
operator files are backed up (`.anvil.bak.N`) only when their generated content
differs; identical files are left in place without another backup. Use
`--compute-a-gpu-uuid`, `--compute-b-gpu-uuid`, or `--tailnet-ip` to override discovery,
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
anvil-serving eval preflight --base-url http://127.0.0.1:<port>/v1 --model <served-model> --dry-run
anvil-serving eval preflight --base-url http://127.0.0.1:<port>/v1 --model <served-model> --confirm
```

For the two-tier `configs/example.toml` shape, preview and then run one
confirmed preflight per tier endpoint, each naming that tier's own `model`
value.

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
`vision.ocr` aliases all select the one configured Omni tier.
The smaller `omni-small` serve is intentionally not routed by default; switching
those aliases remains a human-gated promotion after model-quality review.

## Auth Before Exposure

Loopback-only development does not require built-in auth. Before another
device can reach the router—including through Tailscale Serve while the router
itself remains on loopback—configure auth by env-var name:

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

- Read [Product families and user journeys](PRODUCT-FAMILIES.md) to choose the
  correct authority boundary before operating a different domain.
- Read [Capability meta-router](META-ROUTER.md) for the product and authority
  model, [Architecture](ARCHITECTURE.md) for the system overview, then the
  [meta-router request path](THIN-CAPABILITY-GATEWAY.md) for runtime details.
- Read the [Configuration reference](CONFIGURATION.md) to adapt `configs/example.toml` to your
  serves, and the [CLI reference](CLI.md) for the full command surface.
- Read [Public product and private operator state](OPERATOR-PRIVACY.md) before
  recording real topology or deployment state.
- Read [Device topologies](DEVICE-TOPOLOGIES.md) before spreading gateway, voice, router, or serve roles across more devices.
- Read [Private networking with Tailscale](TAILSCALE-NETWORKING.md) before
  making those roles reachable across devices or from a phone or tablet.
- Read [Model settings](MODEL-SETTINGS-EXAMPLE.md) before serving thinking-by-default models.
- Read [Operator playbooks](OPERATOR-PLAYBOOKS.md) to manage Docker Compose model serves.
- Read [Voice pipeline](VOICE.md) to run STT/TTS lifecycle, the Realtime voice
  server, and model-free harness-node validation.
- Read [Anvil Media commands](cli/media.md) to discover, qualify, run, and
  inspect bounded image/video workflows.
- Read [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) for the reference gateway setup.
