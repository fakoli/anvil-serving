# Configuration Reference

anvil-serving is configured through a single TOML file whose `[server]` and `[router]` tables
declare the front door's auth, the tier topology, and the preset routing map. Start the router with
an explicit file (`anvil-serving router run --config configs/example.toml`) or by mode name
(`anvil-serving router run --mode agentic|flexibility`), which resolves to a mode's config file for you
([ADR-0011](adr/0011-two-mode-operation.md)). A small set of environment variables covers secrets
(configs never hold secret values, only env-var names), mode selection, and front-door resource
caps.

Use `127.0.0.1` in every local URL, never `localhost` (a documented ~21-second Windows IPv6 stall).

## How configuration is loaded

The config is parsed with the stdlib `tomllib` (the reason the package requires Python >= 3.11).
Loading validates eagerly: unknown dialects, malformed tiers, duplicate ids, and presets that
reference unknown tiers are all startup `ConfigError`s, never per-request surprises. No secret is
read at load time — only env-var *names* are recorded.

`anvil-serving router run` selects the config one of two mutually exclusive ways:

- **`--config PATH`** — load that exact file, verbatim. Bypasses the mode system entirely.
- **`--mode agentic|flexibility`** — resolve the global mode of operation to its config file
  ([ADR-0011](adr/0011-two-mode-operation.md)). Exactly one mode's tiers and presets are bound at
  startup; switching modes is a restart, never per-request.

Bare `router run` with no selector at all (no `--config`, no `--mode`, no `ANVIL_MODE`, no
`ANVIL_MODES_CONFIG`) is a usage error — the router never silently boots a default.

Mode resolution uses two precedence chains:

| Question | Precedence (highest first) |
|---|---|
| Which mode is active? | `--mode` flag > `ANVIL_MODE` env > `[modes].active_mode` in the manifest > built-in default (`agentic`) |
| Which file does a mode map to? | `ANVIL_CONFIG_<MODE>` env (e.g. `ANVIL_CONFIG_FLEXIBILITY`) > `[modes].<mode>` manifest entry > built-in default |

The built-in per-mode defaults are `configs/example.toml` (agentic) and
`configs/example-flexibility.toml` (flexibility), resolved relative to a source checkout. A
non-editable (wheel) install does not ship `configs/`, so set `ANVIL_CONFIG_<MODE>`, use a
`[modes]` manifest, or pass an explicit `--config`. An unknown mode from any source is a
`ConfigError` naming the known modes.

`router run` also takes `--host` (default `127.0.0.1`; a non-loopback bind prints a prominent warning —
see [SECURITY.md](https://github.com/fakoli/anvil-serving/blob/main/SECURITY.md)) and `--port`
(default `8000`).

## `[server]`

Optional table controlling front-door token auth
([ADR-0004](adr/0004-router-as-a-service-containerized-and-authed.md)). Absent means auth is off —
the loopback-only default.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auth_env` | string | unset (auth off) | Name of the env var holding the front door's bearer token. Incoming requests must present it as `Authorization: Bearer <token>` or `x-api-key: <token>` (compared constant-time). Must match `^[A-Z][A-Z0-9_]*$`; credential-shaped values (e.g. an AWS `AKIA…` key id) are rejected. |

If `auth_env` is set but the named env var is unset or empty at startup, `router run` fails with a
`ConfigError` rather than silently starting unauthenticated — a configured-but-unresolved
`auth_env` is a misconfiguration, not an opt-out. The `/healthz` liveness route stays
unauthenticated even with auth on (container healthchecks need no token). The conventional token
variable is `ANVIL_ROUTER_TOKEN` (used by
[`configs/example-docker.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/example-docker.toml)
and required by the MCP probe tools).

## `[router]`

The main table: tier topology, preset map, and routing policy knobs.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tiers` | array of tables | required, non-empty | The serving endpoints the router may route to (see below). Duplicate tier ids are rejected. |
| `presets` | table | `{}` | Preset name -> ordered candidate tier-id list (see below). |
| `mapping_version` | string | required, non-empty | Version stamp for this preset->tier mapping; recorded per request in the decision log. |
| `metered_cloud` | list of strings | `[]` | **The billing gate.** The only work-classes permitted to route to a `privacy = "cloud"` tier. Absent or empty means a cloud tier is *never* a routing candidate, regardless of what preset pools include it ([ADR-0001](adr/0001-cloud-cost-and-subscription-auth.md)). |
| `exhaustion_status` | integer (100–599) | `503` | HTTP status returned when every quality-gated tier is exhausted. 503 is the keyless handoff signal a gateway's transport failover can classify as "overloaded" and retry on its own native provider. |
| `cost_sync` | boolean | `false` | When `true`, tiers with unset cost fields are filled from the LiteLLM pricing JSON at load (a network fetch only when the local cache is stale). Explicit config values always win and are never overwritten. |
| `relay_timeout` | number (seconds) | `20.0` | Transport timeout for `privacy = "local"` tier backends. Kept short so a hung or cold local serve fails fast to the next tier. Cloud tiers are unaffected (they keep a 120 s default). A tier's own `timeout` overrides this. |
| `verify_local_min` | boolean | `true` | When `true`, a local tier under an "allow" profile verdict still passes through a minimal commit window (non-empty, not-truncated checks) before the first byte reaches the client, so an empty or truncated local 200 escalates instead of being served silently. Cloud tiers are never affected. |
| `profile_path` | string (path) | unset | Path to a measured quality profile (`profile.json`, written by `python -m anvil_serving.router.profile_bootstrap`). When set, `router run` routes on your measured verdicts instead of the built-in seed profile. A configured-but-unloadable path is a startup `ConfigError` — fail fast, never silently fall back to seeds the operator asked to replace. `~` is expanded. |
| `availability_probe_interval` | number (seconds) | `5.0` | Cache duration for local-tier readiness results. After expiry, the next request rechecks the configured `health_path`, so a recovered serve is automatically readmitted without a router restart. |
| `availability_probe_timeout` | number (seconds) | `1.0` | Per-probe timeout for local-tier readiness checks. A failed probe skips that tier before inference and does not increment its circuit breaker. |

## `[[router.tiers]]`

One table per serving endpoint. A `privacy = "local"` tier is served by the stdlib-`urllib`
`RelayBackend` (auth optional — local vLLM/SGLang serves usually need none); a `privacy = "cloud"`
tier is served by `CloudBackend`, whose credential is resolved from `auth_env` at startup. A cloud
tier whose credential env var is unset is **skipped with a warning, not fatal** — the router starts
bound to the tiers it can serve. If *no* tier can build a backend, startup fails.

### Required keys

| Key | Type | Meaning |
|---|---|---|
| `id` | string, non-empty | Tier id; the routing name used in preset pools (and pinnable from the wire `model` field). Must be unique. |
| `base_url` | string | Endpoint URL. Only `http://` and `https://` schemes are accepted (`file://`, `ftp://`, etc. are rejected to prevent SSRF and local-file access). Use `127.0.0.1` for loopback. |
| `dialect` | `"openai"` \| `"anthropic"` | Wire dialect spoken upstream. Requests arriving in the other dialect are translated. |
| `context_limit` | positive integer | The tier's real context window in tokens; used as a hard routing constraint (gross over-context requests skip the tier). |
| `privacy` | `"local"` \| `"cloud"` | Residency class. `cloud` tiers are additionally gated by `[router].metered_cloud`. |
| `tool_support` | boolean | Whether the tier can serve tool-calling requests; requests carrying tool structure exclude `tool_support = false` tiers. |
| `auth_env` | string | Name of the env var holding this tier's API key. Same validation as `[server].auth_env` (`^[A-Z][A-Z0-9_]*$`, credential-shaped values rejected). For local tiers the variable may be unset — auth is optional on a relay. |

### Optional keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `model` | string | unset | Concrete upstream model id (a cloud provider id, or a local serve's `--served-model-name`). Absent → the request's routing token is forwarded as the model id. A local tier without `model` is probed at startup via the serve's `/v1/models` catalog (the probe normalizes the tier's `base_url` to end in `/v1`): a single advertised id is adopted automatically; an ambiguous catalog (0 or >1 ids) is a `ConfigError`; a network failure is non-fatal and leaves it unset (the loader also prints a warning, since a forwarded preset token will 404 at vLLM/SGLang). |
| `cost_input_per_mtok` | number >= 0 | unset | USD per million input tokens. Set on metered cloud tiers so per-request `cost_usd` can be computed; unset on local tiers (counted as $0). |
| `cost_output_per_mtok` | number >= 0 | unset | USD per million output tokens. Same semantics. |
| `extra_body` | inline table (JSON-serialisable) | unset | Keys merged **verbatim** into the upstream request body — a *hard* override (applied last, so it can deliberately clobber a request-set key). Canonical use: `extra_body = { chat_template_kwargs = { enable_thinking = false } }` to defend a thinking-by-default model against thinking-budget starvation. |
| `extra_body_defaults` | inline table (JSON-serialisable) | unset | Like `extra_body` but applied as a *default* the request can override (e.g. `{ reasoning_effort = "high" }` that a caller may dial down per request). A key in both → `extra_body` wins. |
| `engine` | string | unset | Advisory label for the serving engine behind the tier (`"vllm"`, `"sglang"`, `"llamacpp"`, …). Not routed on; feeds fingerprinting and tooling. |
| `quantization` | string | unset | Advisory quantization label (`"nvfp4"`, `"awq"`, …). Not routed on. |
| `params` | inline table (JSON-serialisable) | unset | Descriptive tier tuning metadata. Unlike `extra_body`, **never forwarded** to the provider. |
| `timeout` | number > 0 (seconds) | unset | Per-tier transport timeout. Overrides `[router].relay_timeout` (local) or the 120 s cloud default for this tier only — e.g. give a slow high-reasoning specialist 90 s. |
| `max_concurrency` | positive integer | unset (no cap) | Per-tier cap on concurrent in-flight requests, enforced by a per-tier semaphore. Excess requests *block* until a slot frees (they are serialised, not rejected) — distinct from the process-global front-door limiter, which 503s. For low-throughput specialized-engine tiers ([ADR-0010](adr/0010-specialized-engine-tier.md)). |
| `health_path` | absolute URL path | unset | Enables cached runtime readiness for this local tier, normally `"/health"`. The router combines this path with `base_url`'s scheme and authority. A failed check records `skipped-unavailable`, sends no inference request, consumes no retry attempt, and does not mutate circuit state. Cloud tiers and tiers without this field preserve the previous no-probe behavior. Query strings, fragments, and relative paths are rejected. |

### Runtime readiness versus configuration

Configured tiers remain part of the topology; readiness only changes their current eligibility.
This avoids rewriting TOML and restarting the router whenever a model container starts or stops.
For example, with `health_path = "/health"`, a deliberately stopped Fast serve is skipped and
the next quality-approved candidate is tried. Once Fast responds successfully after the cache
interval, it returns to the pool automatically. Readiness is independent of response quality:
verification failures still trigger per-request fallback and profile evidence, not endpoint-health
ejection. See [ADR-0016](adr/0016-runtime-tier-readiness.md).

## `[router.presets]`

Maps a preset name to its **ordered candidate tier pool** — the "filter, then rank" input: hard
constraints and the quality profile filter the pool, cost ranks what survives. Each value must be a
non-empty list of unique, known tier ids:

```toml
[router.presets]
chat         = ["fast-local", "heavy-local"]
planning     = ["heavy-local"]
```

The router's canonical caller-facing vocabulary is six presets — `planning`, `quick-edit`,
`review`, `chat`, `chat-fast`, `long-context` — and `GET /v1/models` advertises exactly that
vocabulary. Harnesses select a preset by sending its name in the wire `model` field (optionally
namespaced `anvil/planning`); matching against config keys is case-insensitive. The `model` field
can also name a concrete tier id to pin the request to that tier, but a preset name is checked
*before* a pin, so avoid tier ids that collide with preset names. Any other (or empty) `model`
value falls through to the Tier-0 classifier, which infers a work class from the raw payload; if
the inferred preset is missing from your config, the request collapses to the safer tier. Config
preset names are otherwise free-form — a custom name works as a declared preset for any caller
that sends it, it just isn't advertised by `/v1/models`.

## `[modes]` manifest

An optional, separate manifest file (not auto-discovered — point `ANVIL_MODES_CONFIG` at it)
carrying the default active mode and per-mode config paths. See
[`configs/modes.example.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/modes.example.toml)
and [ADR-0011](adr/0011-two-mode-operation.md).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `active_mode` | `"agentic"` \| `"flexibility"` | unset (falls through to `agentic`) | Default mode when neither `--mode` nor `ANVIL_MODE` is set. |
| `agentic` | string (path) | unset | Config file for agentic mode. Relative paths resolve against the manifest's own directory. |
| `flexibility` | string (path) | unset | Config file for flexibility mode. Same resolution. |

```bash
export ANVIL_MODES_CONFIG=/etc/anvil/modes.toml
anvil-serving router run                          # active_mode from the manifest
anvil-serving router run --mode flexibility       # flag overrides everything
ANVIL_MODE=flexibility anvil-serving router run   # env overrides the manifest default
```

## Environment variables

### Auth tokens

Config files hold env-var *names* only; these are the variables whose *values* carry secrets.

| Variable | Used by | Meaning |
|---|---|---|
| `ANVIL_ROUTER_TOKEN` | front door (via `[server].auth_env` convention) | The router's own bearer/`x-api-key` token. The conventional name — `router token` prints it, and MCP probe tools accept only this name as `api_key_env`. |
| `ANVIL_CONTROLLER_TOKEN` | `anvil-serving controller serve` / `mcp serve --controller-url` | Split-host controller auth token (default `--auth-token-env`). Required on every bind. |
| per-tier `auth_env` names | tier backends | Whatever each tier's `auth_env` names, e.g. `ANTHROPIC_API_KEY` for a cloud tier or `ANVIL_FAST_LOCAL_KEY` for an (optionally authed) local relay. |

### Mode selection

| Variable | Default | Meaning |
|---|---|---|
| `ANVIL_MODE` | unset | Active mode (`agentic` or `flexibility`); overridden by `--mode`, overrides `[modes].active_mode`. |
| `ANVIL_MODES_CONFIG` | unset | Path to the `[modes]` manifest. |
| `ANVIL_CONFIG_AGENTIC` / `ANVIL_CONFIG_FLEXIBILITY` | unset | Per-mode config-path override; beats the manifest and the built-in default. |

### Front-door resource caps

Read once at import; set before starting `router run`.

| Variable | Default | Meaning |
|---|---|---|
| `ANVIL_MAX_BODY_BYTES` | `33554432` (32 MiB) | Maximum request body size. A larger `Content-Length` is rejected with 413 before any body bytes are read. |
| `ANVIL_MAX_CONCURRENCY` | `64` | Maximum requests processed concurrently, process-wide. When all slots are busy the next request gets an immediate 503. (Per-tier caps are the separate `max_concurrency` tier key.) |

### Control plane and paths

| Variable | Default | Meaning |
|---|---|---|
| `ANVIL_CONTROLLER_MAX_BODY_BYTES` | `1048576` (1 MiB) | Controller request body cap. |
| `ANVIL_CONTROLLER_READ_TIMEOUT_SECONDS` | `30.0` | Controller socket read timeout. |
| `ANVIL_WORKSPACE_ROOT` | auto-discovered | Explicit anvil-serving workspace root for MCP tools that write artifacts (validated: must be a real workspace directory). |
| `ANVIL_BENCHMARK_EVIDENCE_DIR` / `ANVIL_EVIDENCE_DIR` | unset | Extra allowed root(s) for MCP benchmark-evidence artifact paths (`os.pathsep`-separated). |

### Other (local serving tools, plugins)

Briefly: `ANVIL_CLAUDE_LOGS` (where `eval usage` reads Claude usage logs; default
`~/.claude/projects`), `ANVIL_MODELS_OUT` / `ANVIL_HF_ROOTS` / `ANVIL_MODEL_DIRS` (`models sync`
output dir and extra scan roots), `ANVIL_CLAUDE_BIN` (pin the `claude` executable used by the
Agent-SDK calibration grader), and `ANVIL_CLOUD_CLASSES` (OpenClaw adapter plugin: comma-separated
preset names allowed to route cloud-side — the harness-side counterpart of `metered_cloud`).

## Shipped example configs

All live in
[`configs/`](https://github.com/fakoli/anvil-serving/tree/main/configs). The top-level keys some of
them carry (`claude_logs`, `hf_extra_roots`, `model_dirs`, `gpu_index`, `served_model_name`)
configure the *local serving tools* (`eval usage`, `models sync`, `serves render`) that share the file; the
router reads only `[server]` and `[router]`.

### `example.toml` — local-only baseline

Two local tiers (`fast-local` on `http://127.0.0.1:30001/v1`, `heavy-local` on
`http://127.0.0.1:30000/v1`), all six presets routed local. Holds zero cloud credentials and incurs
$0 metered billing. **Start here.** Also the built-in default for `--mode agentic`.

### `example-with-cloud.toml` — opt-in metered cloud

Adds a `privacy = "cloud"` Anthropic tier with explicit `cost_*_per_mtok` fields and sets
`metered_cloud = ["planning"]` — the explicit billing decision that makes the cloud tier routable
at all. Start from it only when your harness cannot route cloud traffic itself
([ADR-0001](adr/0001-cloud-cost-and-subscription-auth.md)); without `ANTHROPIC_API_KEY` exported
the cloud tier is skipped at startup and the local tiers still serve.

### `example-flexibility.toml` — relay to an external engine

Flexibility mode ([ADR-0010](adr/0010-specialized-engine-tier.md)): anvil serves nothing itself and
relays to an OpenAI-compatible engine *you* already run (vLLM, llama.cpp, LM Studio, Ollama, …).
Demonstrates the flexibility knobs: `engine`/`quantization` metadata, a per-tier `timeout = 90`,
and `extra_body_defaults = { reasoning_effort = "high" }`. Built-in default for
`--mode flexibility`.

### `example-docker.toml` — containerized router

For the router running as a container next to the serves
([ADR-0004](adr/0004-router-as-a-service-containerized-and-authed.md)): tiers are reached by
compose *service name* (`http://fast:30001/v1`) instead of `127.0.0.1`, and `[server].auth_env =
"ANVIL_ROUTER_TOKEN"` turns front-door auth on.

### `modes.example.toml` — mode manifest

The `[modes]` manifest template described above. Copy it, point `ANVIL_MODES_CONFIG` at your copy.

### `serve-recipes.toml` — serve recipe registry

Not router config: a repeatable record of how each model is served on your hardware (engine, image,
flags, env, measured throughput/VRAM, `verified`/`unverified` status). Consumed by the
`serves`/`eval benchmark external` tooling as the reproducible "pull this model out again" reference.

## Secrets policy

- **Configs contain env-var names, never values.** Every `auth_env` must match
  `^[A-Z][A-Z0-9_]*$`, and credential-shaped strings that happen to fit that charset (AWS
  `AKIA…`/`ASIA…` key ids) are rejected as defense-in-depth. Config loading never reads a secret —
  values are resolved from the environment at backend construction / server start.
- **Values are redacted from logs.** The decision-log/metrics sanitizer masks secret-named fields
  (`api_key`, `authorization`, `token`, …) in every mode, fingerprints prompt bodies (length +
  SHA-256) unless calibration capture is explicitly opted in, and scrubs secret-shaped substrings
  (`sk-…`, `github_pat_…`, `Bearer …`, PEM blocks, …) out of free text. API keys are never
  un-redacted, in any mode.

## See also

- [Getting started](GETTING-STARTED.md) — no-GPU smoke test and first real-tier run
- [Architecture](ARCHITECTURE.md) — module map and request path
- [CLI reference](CLI.md) — the full verb surface
- [Quality-gated router](QUALITY-GATED-ROUTER.md) — presets, tier ladder, verify-fallback, profile
- [SECURITY.md](https://github.com/fakoli/anvil-serving/blob/main/SECURITY.md) — before binding beyond `127.0.0.1`
