# Configuration reference

The router configuration is TOML. It defines local serving endpoints and an
explicit, closed capability vocabulary. Configuration stores environment-variable
names for credentials, never credential literals.

## Minimal direct gateway

```toml
[router]
relay_timeout = 20
availability_probe_interval = 5
availability_probe_timeout = 1

[[router.tiers]]
id = "heavy-local"
base_url = "http://127.0.0.1:30000/v1"
model = "served-model-name"
dialect = "openai"
context_limit = 131072
privacy = "local"
tool_support = true
auth_env = "ANVIL_HEAVY_LOCAL_KEY"
health_path = "/health"

[router.model_routes]
llm.primary = "heavy-local"
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
`extra_body`, and `extra_body_defaults` control relay behavior. `engine`,
`quantization`, and `params` are descriptive serve metadata.

The tier's `model` is the upstream served model name. It is not the public
capability name.

## Purpose models and audio

`[[router.purpose_models]]` maps an exact model name to an embedding or rerank
serve. It is separate from chat aliases and exposes `/v1/embeddings` or
`/v1/rerank`.

`[[router.audio_routes]]` maps a named, operator-owned STT or TTS serve to the
normalized `/v1/audio/transcriptions` or `/v1/audio/speech` gateway. Audio
routes remain separate from chat and purpose-model routing.

## Reference files

- `configs/example.toml`: direct local Heavy and
  voice aliases.
- `configs/example-docker.toml`: the same
  topology for a Compose-network router.

The removed cloud-routing and mode-manifest examples are intentionally not
supported by the direct gateway. Send cloud traffic through the owning harness,
not through this local capability boundary.
