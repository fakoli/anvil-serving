# Configuration reference

The router configuration is TOML. It defines local serving endpoints and an
explicit, closed capability vocabulary. Configuration stores environment-variable
names for credentials, never credential literals.

## Configuration locations

`anvil-serving init` creates a complete, editable starting set in the operator
config home: `$ANVIL_SERVING_HOME`, or `~/.anvil-serving/` when that variable
is unset. It copies the packaged templates mirrored from `configs/` and the
reference manifests into that directory. When an optional path is omitted, the
runtime resolves live configuration from that config home. Explicit
`--config`, `--manifest`, `--registry`, and `--topology` paths always take
precedence. Public checkout examples are never live defaults. Immutable
packaged catalogs, such as the shipped recipe registry, may remain read-only
discovery fallbacks; write operations require an explicit or operator-owned
destination.

For a production/public checkout, point `ANVIL_SERVING_HOME` at an
access-controlled companion repository's host-specific `operator-home/`
directory. Track real topology, deployment overlays, promoted assignments,
and operator recipes there. Keep
credentials outside Git in environment variables or file-backed secret stores.
See [Public product and private operator state](OPERATOR-PRIVACY.md).

The operator files include `router.toml`, `serves.toml`,
`serve-recipes.toml`, `voice.toml`, `host.toml`, `operator-topology.toml`,
Compose files, and `.env.example`. Each existing file is backed up beside the
target as a numbered `.anvil.bak.N` file before `init` replaces it.
Content-identical files are left untouched, so repeated runs do not create
redundant backups, including for `.env.example`.

By default, `init` asks `nvidia-smi` for stable GPU UUIDs and total memory, then
assigns the two largest distinct cards to Compute A and Compute B. Equal-VRAM
cards use canonical UUID ordering, not runtime index, so a reboot cannot swap
their stable roles. Workload capability is independent of A/B placement. It asks
`tailscale ip -4` for this node's tailnet address. Detected values replace the
corresponding template placeholders; unavailable values remain visibly
unconfigured. `--compute-a-gpu-uuid`, `--compute-b-gpu-uuid`, and `--tailnet-ip`
override individual values. Hidden Primary/Auxiliary flags remain temporary
input compatibility aliases only. `--no-detect-host` leaves all host placeholders in
place. A one-GPU machine does not silently assign both concurrent roles to the
same card.

## Operator topology (`operator-topology.toml`)

Topology-aware commands use
`$ANVIL_SERVING_HOME/operator-topology.toml` (default
`~/.anvil-serving/operator-topology.toml`) when `--topology` is omitted.
An explicit path wins. MCP voice tools preserve their existing
`ANVIL_VOICE_TOPOLOGY` override between the explicit argument and config-home
default. Deployment overlays remain explicit through `--topology-overlay`.

`init` writes the topology beside the other operator configuration. On the
reference GPU host it binds the detected Compute A and Compute B GPU UUIDs,
tailnet address, and local command-host identity into that file. On the
model-free Mini it can bind the Mini address when the host is identifiable as
macOS. Values for another host are kept as visible placeholders rather than
invented. Missing or invalid topology files fail closed; repository examples
and packaged documentation topologies are never runtime fallbacks.

## Machine policy (`host.toml`)

`host.toml` holds optional, machine-level lifecycle policy such as the
default-off WSL cache-reclaim settings. It is always resolved from the operator
config home (`$ANVIL_SERVING_HOME/host.toml`, default
`~/.anvil-serving/host.toml`). A missing file or `[cache_reclaim]` table is a
valid disabled policy. Once configured, its fields are validated strictly before
a lifecycle command can start a model operation.

## GPU reservations and operating modes (`serves*.toml`)

Split-mode serves reserve one stable role:

```toml
gpu_role = "dark-compute-a"
vram_mib = 80000
residency = "resident"
groups = ["split-stack"]
```

An exclusive tensor-parallel candidate reserves both roles. `vram_mib` is the
per-role reservation, not pooled memory:

```toml
gpu_roles = ["dark-compute-a", "dark-compute-b"]
vram_mib = 90000
residency = "on-demand"
operating_mode = "dual-gpu-exclusive"
tensor_parallel_size = 2
router_tier = "primary-local"
router_config = "{dir}/router-exclusive.toml"
rollback_router_config = "{dir}/router-split.toml"
native_kv_offload = true
```

The exclusive entry is inert until a separately qualified model recipe adds
it to the private manifest. Ordinary `serves up` refuses to start it. Use
`serves mode preview|enter|leave`, naming an explicit split restore group.
Entry drains and stops every active GPU inference competitor before start;
leaving stops the TP=2 owner before restoring the selected group. Active or
unresolved exclusive ownership blocks manifest and ad-hoc Compose starts before
container mutation. Pre-reservation model experiments are treated as GPU
inference by default so they cannot bypass exclusivity. A genuine CPU-only
sidecar may declare `gpu_inference = false`. The two cards remain separate VRAM
heaps without NVLink or transparent 192 GB pooling.

A routed exclusive owner must declare `router_tier`, `router_config`, and
`rollback_router_config` together. Both direct profiles must route the same
caller aliases to that tier. The exclusive profile's tier model must equal the
target serve's exact `served_name`; the rollback profile must match exactly one
serve in the selected restore group. Mode entry starts and verifies the target,
atomically installs its complete profile, waits for router health, and then
guardedly readmits the tier. Any install or readmission failure stops the target,
restores the rollback profile, and restores the split group. Leave performs the
reverse quiesce, drain, profile, and readmission transaction. An unrouted TP=2
experiment omits all three fields and remains direct-port only.

Both router profile paths are direct dependencies of the operating-mode
manifest. Manifest loading resolves `{dir}` and relative paths against the
manifest directory and fails before lifecycle work if either file is missing.

Set `native_kv_offload = true` only when the serve recipe uses vLLM's native
CPU KV-offload mmap files. This explicit ownership declaration lets `serves
down` run the bounded, two-scan orphan cleanup even when Docker has already
removed the container and its runtime metadata. An absent serve without the
declaration remains a no-op. Cleanup only considers exact
`/dev/shm/vllm_offload_*.mmap` candidates and fails closed when active owners,
live mappings, a changed second scan, or an unavailable postcondition prevent a
safe reclaim.

## Minimal direct gateway

```toml
[router]
relay_timeout = 20
availability_probe_interval = 5
availability_probe_timeout = 1

[[router.tiers]]
id = "primary-local"
base_url = "http://127.0.0.1:30000/v1"
model = "served-model-name"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
health_path = "/health"

[router.model_routes]
llm.primary = "primary-local"
```

`[router.model_routes]` is required. Its normalized aliases are the only chat
`model` values accepted by the gateway. Matching is case-insensitive after
trimming; compatibility prefixes are not accepted. An unknown or missing alias
returns 404.

Each alias must map to one configured local tier. A tier cannot stand in for a
caller-visible alias; use an explicit route table. The route does not create a
fallback pool.

## `[server]`

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
```

`auth_env` is optional for loopback development. When configured, callers must
send its resolved value as a bearer token or `x-api-key`. Expose non-loopback
routers only with token authentication.

## `[router]`

| Key | Default | Meaning |
|---|---:|---|
| `relay_timeout` | `20` | Default upstream request timeout in seconds. |
| `availability_probe_interval` | `5` | Seconds to cache a local tier's readiness result. |
| `availability_probe_timeout` | `1` | Per-readiness-probe timeout in seconds. |
| `availability_probe_max_bytes` | `65536` | Maximum readiness response bytes read. |
| `exhaustion_status` | `503` | Status returned for an unavailable or admission-exhausted selected tier. |

## `[[router.tiers]]`

Every chat tier needs `id`, `base_url`, `model`, `dialect`, `context_limit`,
`privacy = "local"`, `tool_support`, and `auth_env`. `base_url` is an
OpenAI- or Anthropic-compatible base URL; use `127.0.0.1`, never `localhost`,
for same-host serves. Optional `health_path`, `timeout`, `max_concurrency`,
`max_output_tokens`, `extra_body`, and `extra_body_defaults` control relay behavior. `engine`,
`quantization`, and `params` are descriptive serve metadata.

`max_output_tokens` is an optional per-tier runtime safety ceiling. When a
caller requests a larger completion budget, the router forwards the request
with the configured ceiling and returns `Warning`, `X-Anvil-Warning`,
`X-Anvil-Max-Tokens-Requested`, and `X-Anvil-Max-Tokens-Applied` response
headers. It also records `served_output_clamped` in the metadata-only decision
trail. Tiers without this field preserve caller and upstream behavior.

The tier's `model` is the upstream served model name. It is not the public
capability name.

### Capacity metadata

`GET /v1/models/capacity` exposes an allowlisted subset of descriptive metadata
from `params.capacity`, joined with readiness and bounded live `/metrics` values
from the serving engine:

```toml
engine = "vllm"
quantization = "nvfp4"
max_concurrency = 1
params = { capacity = { gpu_role = "dark-compute-a", gpu_name = "NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition", gpu_memory_total_mib = 97887, model_memory_gib = 73.22, kv_cache_capacity_tokens = 571950, scheduler_max_num_seqs = 1, image_limit = 1, video_limit = 0 } }
```

The allowlisted capacity keys are `gpu_role`, `gpu_name`,
`gpu_memory_total_mib`, `model_memory_gib`, `kv_cache_capacity_tokens`,
`scheduler_max_num_seqs`, `image_limit`, and `video_limit`. Other `params`
values are never returned. These values are operator-declared measurements,
not runtime discovery.

The endpoint accepts `model` and `gpu_role` filters. Optional `images`,
`input_tokens`, `image_tokens`, and `output_tokens` parameters evaluate a
request scenario. Image count alone cannot establish context use because image
resolution and preprocessing determine visual token count, so a scenario with
images reports context admissibility only when total `image_tokens` is supplied.

`params.capabilities` is the allowlisted client-facing declaration used by
`GET /v1/models/capabilities`: `modalities`, nested `thinking` fields
(`supported`, `default`, `caller_override`, and optional `max_tokens`),
`images_per_request`, `video_per_request`, and a nested `compat` block. The
`compat` block mirrors OpenClaw's provider-model shape and currently carries
`supportsUsageInStreaming` (a bool): set it to `true` when a tier's streaming
path can emit a usage chunk, so metering clients know to look for one.

`params.fingerprint` supplies optional identity evidence for
`GET /v1/models/fingerprints`: `model_revision`, `engine_version`,
`image_digest`, and `config_fingerprint`. Unknown fields in either section stay
private. The endpoint reports missing evidence as `null`; it never fabricates a
digest or revision.

See the
[router observability API](THIN-CAPABILITY-GATEWAY.md#router-observability-api)
for the full read-only surface and privacy boundary.

## Purpose models and audio

`[[router.purpose_models]]` maps an exact model name to an embedding or rerank
serve. It is separate from chat aliases and exposes `/v1/embeddings` or
`/v1/rerank`. See [Embeddings and reranking](PURPOSE-MODELS.md) for the field
contract, request examples, and why this surface routes by model name.

`[[router.audio_routes]]` maps a named, operator-owned STT or TTS serve to the
normalized `/v1/audio/transcriptions` or `/v1/audio/speech` gateway. Audio
routes remain separate from chat and purpose-model routing.

## Reference files

- `configs/example.toml`: direct local Primary and
  voice aliases.
- `configs/example-docker.toml`: the same
  topology for a Compose-network router.

These files are public templates, not live deployment defaults. They contain
generic identities and must not be edited to match an operator workstation.

The removed cloud-routing and mode-manifest examples are intentionally not
supported by the direct gateway. Send cloud traffic through the owning harness,
not through this local capability boundary.
