# Configuration reference

The router configuration is TOML. It defines local serving endpoints, an
explicit closed capability vocabulary, and which authority supplies mutable
served-model metadata for each tier. Configuration stores environment-variable
names for credentials, never credential literals.

The capability route and metadata authority are separate decisions. The
operator always owns `alias -> tier -> endpoint`; `metadata_source` decides
only whether the router config or that already-selected inference service owns
the tier's served model, context, and allowlisted runtime facts. No setting in
this file enables intent routing, candidate ranking, or fallback. See
[Capability meta-router](META-ROUTER.md).

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

## Lifecycle events (`events.toml`)

Lifecycle event recording is optional and disabled when
`$ANVIL_SERVING_HOME/events.toml` is absent or its `[events]` table does not set
`enabled = true`. When enabled, successful state changes from `serves up`,
`serves down`, `serves profile apply`, and `serves promote` invoke the
stdlib-only `anvil-events` CLI. Dry runs, failed operations, already-satisfied
serves, and no-op profile transitions do not create false history.

```toml
[events]
enabled = true
command = "anvil-events"
node = "node-a"
producer = "node-a:anvil-serving"
root = "/var/lib/anvil/events"
```

`command` is one executable name or path and is invoked without a shell. `node`
and `producer` are public event-envelope identities; the producer's first token
must be the node. `root` is the absolute local v2 SQLite store root shared with
the node's `anvil-events serve` process. Real operator identity and paths belong
only in the private operator home. The retired v1 fields `host`, `nats_url`, and
`nats_url_env` are rejected so an old configuration cannot silently target the
removed JSONL/`emit` contract.

The child invocation is `anvil-events --root ROOT record KIND ...`. It sends the
JSON payload on standard input, assigns a fresh operation key, and requires
machine-readable local-acceptance evidence from the CLI. The command performs
no broker I/O. A successful invocation therefore means **committed locally**,
not delivered or acknowledged by JetStream; the independent `anvil-events`
delivery worker owns retry and PubAck evidence. If the executable is missing,
times out, cannot commit SQLite, or returns malformed acceptance evidence, the
lifecycle action may already be applied; the command returns non-zero and
reports that the change was applied but its event was not recorded.

The seam records the frozen lifecycle kinds `serve.up`, `serve.down`,
`profile.enter`, `profile.leave`, `promote.applied`, and
`promote.rolled_back`. A multi-tier promotion emits one promotion record per
declared affected tier while the promotion lock is still held.

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

`serves up` runs a post-start storage write guard on every docker serve:
each read-write named-volume mount is probed as the container's real runtime
identity before the readiness wait, because a serve can answer its health
endpoint while unable to write its volumes (pre-created volume layouts defeat
Docker's empty-volume ownership donation, leaving root-owned directories
under a non-root workload). A denied mount on an unshared volume is repaired
in place — re-owned to the runtime identity and the container restarted so
failed initialization re-runs — then re-verified. Declare volumes this serve
deliberately shares with other containers:

```toml
shared_volumes = ["comfyui-user"]
```

A declared-shared volume is never auto-re-owned: sharing is a deployment
decision and so is its ownership, so the guard fails the serve and prints the
manual command instead. A volume found shared at runtime without a
declaration is reported as a topology fault in its own right. Bind mounts and
read-only mounts are outside the guard's scope; deliberately non-writable
storage should be mounted read-only.

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

### Metadata authority modes

| Mode | Authority for mutable served-model facts | Use when |
| --- | --- | --- |
| `configured` (default) | Router configuration | Router and inference settings are released together. |
| `upstream` | The already-selected single-model inference service | The model or context can change independently at a stable endpoint. |

Both modes use the same exact alias-to-tier route. The difference affects
effective metadata, request admission, and the served model id relayed to that
endpoint; it never affects tier selection.

### Inference-owned model metadata

Use `metadata_source = "upstream"` when an operator changes the model or
context at a single-model OpenAI-compatible inference endpoint independently
of the router:

```toml
[[router.tiers]]
id = "secondary-local"
base_url = "http://100.64.0.10:39038/v1"
dialect = "openai"
metadata_source = "upstream"
privacy = "local"
tool_support = true
auth_env = "ANVIL_SECONDARY_LOCAL_KEY"
health_path = "/health"
max_concurrency = 1

[router.model_routes]
llm.secondary = "secondary-local"
```

Do not set `model`, `context_limit`, `engine`, `quantization`,
`model_identity`, or `params.fingerprint` on an upstream-owned tier. The
router probes health and requires exactly one entry from `GET /v1/models`.
It reads the served identity and context from that model card when available.
For llama.cpp it also reads the bounded, read-only `GET /props` response for
`n_ctx`, quantization, build, slot, and modality facts. A missing context,
ambiguous catalog, malformed value, or identity disagreement fails closed.

The result is cached for `availability_probe_interval` seconds. After that
bounded interval, replacing the model or context at the same endpoint updates
request admission and the router's model metadata without a router config
edit. The public alias and its exact tier mapping do not change, and a failed
request is never retried against another model. `max_output_tokens`,
`tool_support`, `params.capabilities`, and media-admission limits remain router
safety policy rather than inferred model claims.

## `[server]`

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
```

`auth_env` is optional for loopback development. When configured, callers must
send its resolved value as a bearer token or `x-api-key`. Expose non-loopback
routers only with token authentication.

### Token persistence contract

Token configuration names environment variables; the values must also survive a
host reboot. Store each token line in the gitignored operator `.env` chain —
`$ANVIL_SERVING_HOME/.env`, then `~/.env` — and treat the process environment
as an override, never the only copy. Router and controller token resolution
reads the shell environment first and falls back through that chain, so a
freshly rebooted host can authenticate without re-exporting variables in a
live shell. Refusals name every location that was checked. Values never appear
in tracked files (ADR-0032).

### Durability sinks (ADR-0033)

| Key | Default | Meaning |
|---|---:|---|
| `admission_state_path` | unset | Opt-in persisted tier-quiesce intent. Quiesced tiers (except promotion-owned quiescence) are restored quiesced at boot; readmission still requires the health+identity gate. A corrupt file refuses to serve. |
| `decision_log_path` | unset | Opt-in append-only, metadata-only JSONL of decision records (timestamped), size-capped with one rotated generation. |

Both point at writable paths on the router state volume in containerized
deployments. Unset keys mean no file I/O.

## `[router]`

| Key | Default | Meaning |
|---|---:|---|
| `relay_timeout` | `20` | Default upstream request timeout in seconds. |
| `availability_probe_interval` | `5` | Seconds to cache a local tier's readiness result. |
| `availability_probe_timeout` | `1` | Per-readiness-probe timeout in seconds. |
| `availability_probe_max_bytes` | `65536` | Maximum readiness response bytes read. |
| `exhaustion_status` | `503` | Status returned for an unavailable or admission-exhausted selected tier. |

## `[[router.tiers]]`

Every chat tier needs `id`, `base_url`, `dialect`, `privacy = "local"`,
`tool_support`, and `auth_env`. The default `metadata_source = "configured"`
also requires `model` and `context_limit`. An upstream-owned tier instead
requires `metadata_source = "upstream"` and `health_path`, and omits both
values as described above. `base_url` is an
OpenAI- or Anthropic-compatible base URL; use `127.0.0.1`, never `localhost`,
for same-host serves. Optional `health_path`, `timeout`, `max_concurrency`,
`max_output_tokens`, `context_admission`, `extra_body`, and
`extra_body_defaults` control relay behavior. `engine`,
`quantization`, and `params` are descriptive serve metadata.

`context_admission = "estimate"` is the default. It rejects a text request
before relay when the router's stdlib-only conservative token estimate exceeds
the tier's context window. For a directly selected, exact-identity local serve
whose inference engine enforces context with the model's real tokenizer,
`context_admission = "upstream"` delegates that one text boundary check to the
same selected endpoint. This opt-in requires either `model_identity = true` or
`metadata_source = "upstream"`; it never enables a retry, fallback, or alternate
model. Router usage and decision counters remain estimates. Explicitly enabled
media admission remains router-enforced because it also owns declared visual
token and media-count policy.

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
not runtime discovery. On an upstream-owned tier, keep only stable host or
router-policy facts here; do not copy mutable model, KV-cache, or scheduler
values that can drift when the inference service changes.

`image_limit` and `video_limit` become enforced request admission controls only
when `media_admission_enabled = true`. An enabled policy must also provide
non-negative `image_tokens_estimate` and `video_tokens_estimate` values. The
router then rejects media-count overflow before contacting the selected tier
and reserves the estimates when checking context headroom. A declared zero
limit therefore rejects the first matching media block; without the explicit
enable flag, the values remain metadata for backward compatibility.

The endpoint accepts `model` and `gpu_role` filters. Optional `images`,
`input_tokens`, `image_tokens`, and `output_tokens` parameters evaluate a
request scenario. Image count alone cannot establish context use because image
resolution and preprocessing determine visual token count, so a scenario with
images reports context admissibility only when total `image_tokens` is supplied.

`params.capabilities` is the allowlisted client-facing declaration used by
`GET /v1/models/capabilities`: `modalities`, nested `thinking` fields
(`supported`, `default`, `caller_override`, and optional `max_tokens`),
`images_per_request`, `video_per_request`, and a nested `compat` block. The
`compat` block carries OpenClaw-compatible capability declarations:
`supportsUsageInStreaming`, `supportsStrictMode`, and `supportedReasoningEfforts`
(the exact OpenClaw key, an ordered set of lowercase effort labels such as
`["low", "high", "max"]` that a tier honors). Set `supportsUsageInStreaming` to
`true` when a tier's streaming path can emit a usage chunk (so metering clients
look for one), `supportsStrictMode` to `true` when a tier honors strict
JSON-schema structured output, and enumerate the reasoning-effort levels the tier
honors under `supportedReasoningEfforts`. OpenClaw treats the effort list as a
membership set, not an ordered ladder; order is cosmetic. This is a declaration
surface: an operator maps these to the model's `compat` in OpenClaw config
(OpenClaw does not auto-read this endpoint).

`params.fingerprint` supplies optional identity evidence for
`GET /v1/models/fingerprints`: `model_revision`, `engine_version`,
`image_digest`, and `config_fingerprint`. Unknown fields in either section stay
private. The endpoint reports missing evidence as `null`; it never fabricates a
digest or revision. An upstream-owned tier forbids this configured fingerprint
and instead reports allowlisted live values under `served_configuration`.

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

## Named media workflows

Media generation is configured separately from chat, purpose-model, and audio
routes. Each immutable workflow descriptor names one stable ID and version,
one media kind, one bounded parameter schema, one graph digest, and exactly one
logical media-service target. Operator topology resolves that target to one
resource owner; the descriptor never contains a private endpoint or fallback
list.

The first-release limits and qualification blockers are frozen in
[ADR-0041](adr/0041-initial-media-workflows-and-policy.md). Public workflow
descriptors are candidates until compatibility, functional, capacity,
artifact, rollback, license, and independent quality evidence make them
available. Missing values remain blockers rather than runtime defaults.

Gateway media credentials are environment-variable references and carry
explicit scopes such as `media:read`, `media:submit`, and `media:cancel`.
Lifecycle approval uses the existing operator confirmation contract; a media
credential never inherits controller-wide authority. Artifact storage and job
state paths belong in the private operator home and are not exposed by workflow
discovery.

When `[server].media_principal` enables the gateway surfaces, the process reads
the media runtime only from environment variables:

| Variable | Purpose |
| --- | --- |
| `ANVIL_MEDIA_BACKEND_URL` | Required ComfyUI adapter endpoint selected by operator topology. |
| `ANVIL_MEDIA_WORKFLOW_REGISTRY` | Optional path to the pinned public workflow registry. |
| `ANVIL_MEDIA_STATE_DB` | Durable job and lifecycle database shared with the resource-owning controller. |
| `ANVIL_MEDIA_ARTIFACT_ROOT` | Private retained-artifact directory. |
| `ANVIL_MEDIA_CONTROLLER_URL` | Optional controller MCP origin used to preview a cold worker start. |
| `ANVIL_MEDIA_CONTROLLER_TOKEN` | Controller credential paired with the controller URL; the value is never stored or returned. |

The controller URL and token must be set together. If a selected worker is
cold, the gateway atomically reserves the job, asks only for a dry-run
`media_worker_prepare` receipt, and returns `awaiting_approval` with a bounded
operator action. It cannot apply that action. An operator must invoke the typed
controller tool with `dry_run=false`, `confirm=true`, and
`human_approved=true`; the caller then retries the same workflow and
idempotency key after the worker becomes ready. The gateway submits that one
durable job once and its reconciliation loop captures completed output into the
opaque artifact store. Missing controller configuration fails the reserved job
closed without contacting the backend.

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
