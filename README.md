<div align="center">

![Anvil Serving - explicit local AI capabilities](docs/assets/banner.png)

# anvil-serving

> **Operate, qualify, and expose local AI capabilities through explicit contracts.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Source Version](https://img.shields.io/badge/source-1.0.0-blue.svg)](CHANGELOG.md)
[![Docs](https://img.shields.io/badge/docs-fakoli.github.io%2Fanvil--serving-blue.svg)](https://fakoli.github.io/anvil-serving/)

</div>

Anvil Serving is one umbrella product for local Model Serving, the Capability
Gateway, Evaluation & Evidence, Anvil Voice, Anvil Media, and Control Plane &
Fleet operations. Those families share one package, CLI, topology, safety
contract, evidence policy, and release line. Anvil Voice and Anvil Media are
first-class branded domains inside the umbrella, not separate products.

The gateway family is an explicit **capability meta-router**: callers use a
stable capability alias, operators map that alias to exactly one tier, and the
selected inference service may report mutable facts about the model and
context it currently serves. Its request implementation remains deliberately
thin. There is no request classifier, quality-profile router, semantic
fallback, cloud escalation, or hidden substitute model.

The reference topology has two equivalent RTX PRO 6000 Blackwell Max-Q GPUs.
In split mode, compatible LLM, Omni, voice, purpose-model, and ComfyUI workloads
reserve Compute A or Compute B independently. In `dual-gpu-exclusive` mode,
one explicitly declared TP=2 serve owns both cards and every other GPU
inference workload is offline. Capability aliases remain independent of that
placement. The gateway keeps authentication, dialect translation, streaming,
readiness, admission, and decision evidence consistent across those
capabilities. See [Product families and user journeys](docs/PRODUCT-FAMILIES.md)
for the authority boundary and the ordered path through each family.

## Product families

| Family | User outcome | Primary commands |
| --- | --- | --- |
| **Model Serving** | Pin and operate reproducible model serves. | `init`, `models`, `serves` |
| **Capability Gateway** | Expose exact authenticated capability aliases. | `router` |
| **Evaluation & Evidence** | Prove compatibility and retain benchmark evidence. | `eval` |
| **Anvil Voice** | Operate qualified STT, TTS, and realtime voice paths. | `voice` |
| **Anvil Media** | Run bounded named image/video workflows with durable artifacts. | `media` |
| **Control Plane & Fleet** | Resolve ownership and operate declared hosts and integrations. | `topology`, `controller`, `mcp`, `fleet`, `host` |

The installed product map is read-only and machine-readable:

```bash
anvil-serving product families
anvil-serving product journey anvil-media
anvil-serving product journey control-plane-fleet --json
```

## Capability meta-router contract

```toml
[router.model_routes]
llm.primary = "primary-local"
llm.voice = "omni-local"
vision.ocr = "omni-local"
vision.general = "omni-local"
vision.video = "primary-local"
```

Send one of those aliases as the chat `model`. Matching is case-insensitive
after trimming; compatibility prefixes are not accepted. `/v1/models` advertises the
configured aliases plus each alias's effective `context_window` and
`max_output_tokens`. A tier may keep its served model and context explicitly
configured, or opt into bounded metadata reported by its inference service.
The alias-to-tier route stays static in both modes. Unknown or missing chat
aliases return 404. An unavailable selected tier returns an exhaustion error,
not an alternate model.
The authenticated `/v1/models/capacity` endpoint joins declared model/GPU
capacity with bounded live engine telemetry; it does not operate a serve or
grant the router GPU-device access.
Related authenticated endpoints expose declared capabilities and fingerprints,
router build/config identity, bounded-buffer statistics, request traces, and
Prometheus gauges. See the
[router observability API](docs/THIN-CAPABILITY-GATEWAY.md#router-observability-api).

Purpose models and audio are equally explicit: embeddings and reranking use
their configured model names on dedicated endpoints, while STT/TTS use
operator-configured audio routes. ComfyUI is lifecycle-managed rather than a
chat capability. Its named media workflows may publish bounded, caller-selected
quality profiles such as `draft`, `standard`, and `high`; each profile resolves
to exact parameters inside the same workflow and never selects another model,
host, backend, or provider. Durable media jobs report gateway-observed phase
latency, and image artifacts within the six-MiB binary transport bound can
return as native MCP image content.

The word **meta** describes the separation between a stable caller contract
and the mutable configuration behind it. It does not mean that Anvil Serving
chooses among models. The complete request path is:

1. The caller chooses a declared capability alias such as `llm.secondary`.
2. Operator configuration maps that alias to exactly one tier and endpoint.
3. The selected tier supplies configured metadata, or its one inference
   service supplies bounded live model metadata when explicitly enabled.
4. The router validates readiness and admission, then relays to that same
   endpoint or fails closed.

See [Capability meta-router](docs/META-ROUTER.md) for the authority model and
the product decisions that keep dynamic metadata separate from dynamic route
selection.

## Quick start

Python 3.11+ is the only runtime prerequisite. Docker and a GPU are required
only for real local model serves.

```bash
pip install -e .
anvil-serving product families
anvil-serving init
anvil-serving serves groups
anvil-serving serves up SERVE_NAME --dry-run
anvil-serving serves up SERVE_NAME --confirm
anvil-serving serves mode status
anvil-serving router run
```

`init` writes the packaged operational manifests to `~/.anvil-serving`. It
detects NVIDIA GPU UUIDs with `nvidia-smi`, assigns stable Compute A and Compute
B roles, and resolves the host's Tailscale IPv4 address. Capacity is sorted
largest-first; equal-capacity cards use canonical UUID ordering so runtime-index
changes cannot swap the roles. Use `--compute-a-gpu-uuid` and
`--compute-b-gpu-uuid` to override discovery, `--no-detect-host` to keep
placeholders, `--out-dir` to choose another location, or `--single-model` for a
focused one-model scaffold. `serves up` is the canonical bring-up path for
models and other manifest-owned resources. Rerunning `init` leaves
content-identical files untouched; only changed files receive numbered backups
before replacement. Preview the resolved operation before confirming it.

With the selected serves running, call the gateway:

```bash
curl -s http://127.0.0.1:8000/v1/models
curl -s 'http://127.0.0.1:8000/v1/models/capacity?model=llm.primary&images=1&image_tokens=2048'
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"llm.primary","messages":[{"role":"user","content":"hello"}]}'
```

Use `anvil-serving eval preflight` before mapping a real model to an alias, and
record capacity and quality evidence with `anvil-serving eval benchmark`. A
mapping is an exposure decision, not a model promotion claim.

## What it provides

| Surface | Purpose |
|---|---|
| `anvil-serving product` | Read-only family, boundary, and ordered-journey discovery. |
| `anvil-serving router run` | Authenticated Anthropic/OpenAI-compatible capability meta-router. |
| `anvil-serving serves` | Compose-backed lifecycle, GPU reservations, and split/exclusive TP=2 mode transactions. |
| `anvil-serving eval preflight` | Functional qualification of a concrete endpoint. |
| `anvil-serving eval benchmark` | Capacity and quality evidence collection. |
| `anvil-serving models` | Model cache, source, and serve-recipe management. |
| `anvil-serving voice` | Operator-owned STT/TTS, bridge, Realtime, and voice benchmark lifecycle. |
| `anvil-serving media` | Named image/video workflows, qualification, durable jobs, cancellation, and opaque artifacts. |
| `anvil-serving mcp serve` / `controller` | Structured same-host or private control-plane access. |
| `anvil-serving topology` / `fleet` / `host` | Ownership resolution, fleet parity/drift, and supported host utilities. |

The reference split-host control plane runs the controller in the dedicated
Linux `controller` image on Fakoli Dark and exposes it through host-owned
Tailscale Serve. Fakoli Mini runs only the MCP stdio bridge used by OpenClaw.
That bridge bundles the official TypeScript MCP SDK and accepts both the
legacy initialize era through `2025-11-25` and the stateless `2026-07-28`
era. Its authenticated downstream connection to Dark is pinned to
`2026-07-28`; the controller itself never exposes a legacy endpoint. Remote
MCP proxy mode therefore requires Node.js 20+, while the Python router,
controller, and ordinary CLI remain stdlib-only.

## Documentation

- [Start here for the next internet model recipe](START_HERE.md)
- [Getting started](docs/GETTING-STARTED.md)
- [Product families and user journeys](docs/PRODUCT-FAMILIES.md)
- [Capability meta-router](docs/META-ROUTER.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIGURATION.md)
- [Public product and private operator state](docs/OPERATOR-PRIVACY.md)
- [Meta-router request path](docs/THIN-CAPABILITY-GATEWAY.md)
- [CLI reference](docs/CLI.md)
- [Operator playbooks](docs/OPERATOR-PLAYBOOKS.md)
- [Fakoli Mini to Dark remote control](examples/fakoli-dark/REMOTE-CONTROL.md)
- [Voice pipeline](docs/VOICE.md)
- [Anvil Media commands](docs/cli/media.md)
- [Benchmarks](docs/benchmarks/index.md)
  - [Context, agentic, and SWE benchmark jobs](docs/benchmarks/context-agentic-swe.md)
  - [RTX PRO 6000](docs/benchmarks/hardware/rtx-pro-6000.md)
  - [RTX 5090 historical measurements](docs/benchmarks/hardware/rtx-5090.md)
- [Benchmark run catalog](docs/benchmarks/runs.md)
- [OpenClaw integration](docs/OPENCLAW-INTEGRATION-SPEC.md)
- [ADRs](docs/adr/README.md)

## Security and operating boundaries

- Treat every tracked file as public. Keep real topology, active promotions,
  machine paths, and working evidence in a private operator repository selected
  through `ANVIL_SERVING_HOME`.
- Use `127.0.0.1`, never `localhost`, for same-host URLs.
- Keep router authentication enabled before exposing it beyond loopback.
- Store credentials only through environment-variable references.
- Model workloads deny network egress by default. Compose serves must use only
  `internal: true` networks; managed recipes use an Anvil-owned internal bridge.
  Stage model assets before serving. Model recipes cannot opt out. Egress is
  limited to explicitly non-inference capability, media, or voice gateways with
  an audited role and durable reason. See
  [ADR-0043](docs/adr/0043-model-workloads-deny-network-egress-by-default.md).
- Treat readiness and preflight as different checks: readiness says a serve can
  receive traffic; preflight and benchmark evidence establish whether it should.
- Fakoli Mini is model-free in the reference topology. Its local audio proxy
  ports forward to Dark; they do not make Mini a serving host.

See [SECURITY.md](SECURITY.md) for the threat model and reporting policy.
