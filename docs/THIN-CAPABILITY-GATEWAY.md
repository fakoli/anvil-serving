# Capability meta-router request path

anvil-serving exposes measured local capabilities through one authenticated,
OpenAI- and Anthropic-compatible endpoint. It does not infer workload intent,
rank candidate models, apply routing quality profiles, or retry another model.
The separate named-media surface may resolve an explicit caller-selected
quality profile to locked parameters within one workflow; that is not route or
model selection.

At the product level this is a **capability meta-router**: it keeps a stable
capability contract in front of a configured tier whose concrete serving
metadata may change. At the implementation level it remains a **thin
capability gateway**: the request path authenticates, validates, translates,
admits, streams, and relays without gaining selection or lifecycle authority.
See [Capability meta-router](META-ROUTER.md) for the complete authority model.

## Contract

`[router.model_routes]` is the complete chat-model vocabulary. The gateway
normalizes a caller's `model` value by trimming and lowercasing. Each configured
alias maps to exactly one
local tier. An unknown or missing alias is a caller error (404); it is never
classified or redirected.

```toml
[router.model_routes]
llm.primary = "primary-local"
llm.voice = "auxiliary-local"
vision.ocr = "ocr-local"
vision.general = "vision-local"
vision.video = "video-local"
```

The chosen tier remains subject to its configured or inference-reported
context, declared tool capabilities, health/readiness probes, and concurrency
admission. If it is unavailable or
admission is exhausted, the gateway returns its configured exhaustion response;
it does not select a substitute tier.

The alias-to-tier map is always configured and exact. A tier may optionally set
`metadata_source = "upstream"` so its one OpenAI-compatible inference service
owns the current served-model identity and context. The router refreshes those
facts through bounded readiness metadata and fails closed when the service is
ambiguous or incomplete. This is runtime configuration discovery, not inferred
routing, model selection, or lifecycle control.

### Qualified same-host replicas

One selected alias may target a declared 2–16-member replica tier on one host.
The alias still selects exactly that logical tier; member scheduling happens
only after the normal request gates, member readiness, and admission checks.
It never selects another alias, host, model, deployment recipe, or serving
runtime, and it never creates a fallback path.

Replica tiers use `round_robin` unless `replica_strategy = "capacity"` is
explicitly configured. Capacity mode orders already eligible members by exact
local reservation ratio, then treats non-fresh upstream pressure as unknown,
then uses normalized pressure and a rotating stable member-ID tie break. For
example, unknown upstream pressure ranks conservatively behind fresh pressure
at the same local ratio; it does not turn a missing metric into zero load.
Read [Configuration](CONFIGURATION.md#qualified-same-host-replicas) for the
closed tier/member contract.

Selection reserves one compound tier/member lease and invokes one member once.
There is no retry, replay, or second selection after dispatch. Buffered and
SSE terminal paths retain that lease through terminal close, then release it
exactly once. Current capacity views read bounded cached signals without
starting metric collection; DecisionLog scheduler scores are selection-time,
pre-reservation history rather than current capacity.

## Authority boundary

| Fact or decision | Authority |
| --- | --- |
| Caller-visible capability alias | `[router.model_routes]` |
| Alias-to-tier mapping and endpoint | Operator-owned router configuration |
| Mutable served model and context | Router config, or the selected inference service in upstream mode |
| Tool, media, output, readiness, and concurrency policy | Router configuration |
| Model quality and promotion | Recorded evaluation plus a human-gated operator transaction |

The router combines these authorities only after the alias has selected its
one tier. An upstream response can update the description of that tier; it
cannot change the route.

## What remains in the request path

- bearer or API-key authentication at the front door;
- Anthropic Messages, OpenAI Chat Completions, and supported stateless
  Responses translation;
- upstream streaming relay (SSE), cancellation, timeout propagation, and
  provider error normalization;
- tier readiness, per-tier and front-door admission controls; and
- metadata-only `DecisionLog` records for success, error, and disconnect.

The authenticated read-only `GET /v1/models/capacity` surface reports configured
chat-model capacity and, when available, current vLLM scheduler/cache signals.
It reads the serve's ordinary `/metrics` endpoint; the router does not receive
Docker-socket access, GPU-device access, or authority to change model
lifecycle. Unknown live values remain unavailable rather than being inferred
from GPU memory. Optional request-scenario arithmetic checks configured image
and context limits, but requires caller-supplied `image_tokens` for requests
that include images.

For capacity replica tiers, those signals are bounded per-member observations:
fresh values are at most five seconds old; stale, failed, and unknown values do
not establish availability, qualification, or a deployment claim. The cache is
bounded to configured members and two workers, and a capacity read or
Prometheus scrape does not refresh it.

Additional authenticated read-only surfaces expose model capabilities,
fingerprints, router status, current-buffer statistics, request traces, and
Prometheus gauges. They project existing configuration/readiness/decision
metadata and do not add model selection or lifecycle authority.

Purpose models are deterministic model-name routes on `/v1/embeddings` and
`/v1/rerank`. Audio routes are deterministic operator-selected STT/TTS routes
under `/v1/audio/*`. ComfyUI is lifecycle-managed separately and is not a chat
model route. Optional `/mcp`, `/a2a`, Agent Card, and opaque artifact handlers
share the authenticated origin but adapt to a durable operation service rather
than the inference relay. They accept only named allowlisted workflows, and
all lifecycle mutations remain typed controller operations executed by the
declared resource owner.

## Router observability API

The router exposes authenticated, read-only operational views over its explicit
model configuration, readiness state, bounded decision log, and serving-engine
metrics. Except for `GET /healthz`, the normal router bearer token or
`x-api-key` is required. Operational responses use
`Cache-Control: no-store`.

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | OpenAI-shaped configured aliases with effective configured or observed context and output limits for client discovery. |
| `GET /v1/models/capacity` | Declared GPU/model capacity, bounded live engine signals, and request-scenario arithmetic. |
| `GET /v1/models/capabilities` | Tools, modalities, thinking behavior, context, and multimodal limits. |
| `GET /v1/models/fingerprints` | Configured identity evidence or allowlisted model configuration observed from an upstream-owned inference service. |
| `GET /v1/router/status` | Package version, uptime, aliases, tier counts, and a secret-free effective-config hash. |
| `GET /v1/stats` | Tokens, bytes, outcomes, finish counts, and measured phase-latency percentiles over the current decision-log buffer. |
| `GET /v1/requests/{request_id}` | Metadata-only trace, preferring the gateway-generated identifier over legacy caller correlation. |
| `GET /metrics` | Prometheus gauges for router-buffer aggregates and current model capacity/load. |

The OpenAI-compatible discovery list exposes only aliases and
their effective context/output limits; it does not expose upstream identity,
readiness, or private topology. Authenticated model endpoints and `/v1/stats`
accept `model=<configured-alias>`. Capacity also
accepts `gpu_role`, `images`, `input_tokens`, `image_tokens`, and
`output_tokens`. Stats and Prometheus accept `limit` from 1 through 10,000.
Unknown aliases return 404; unsupported or malformed parameters return 400.

The decision log is a bounded in-memory ring buffer. Records carry creation
timestamps, but the aggregate API does not query persisted historical windows.
`/v1/stats` reports
`scope = "current_decision_log_buffer"` instead of accepting a historical
window. Prometheus values are gauges because eviction or restart can reduce
them. Live engine-metric failures make those values unavailable; they do not
fail routing or expose raw upstream errors.

Inference responses return a gateway-generated `X-Anvil-Request-Id`. Use
`router diagnose --request-id` to inspect its terminal record and separately
labeled current process metadata without replaying a request. Chat measurements
distinguish first emitted content, readiness-check time, upstream duration,
finish reason, output limits, and upstream versus estimated token usage. See
[request diagnostics](ROUTER-DIAGNOSTICS.md) for the exact semantics and limits.

Responses omit prompts, responses, message content, audio, transcripts,
upstream URLs, host identifiers, auth environment names, credentials, and
arbitrary tier parameters. Lifecycle and model promotion remain behind the
serving/controller operations; no read-only endpoint can mutate them.

## What is intentionally absent

There are no inference intent presets, classifier, routing policy/profile
selection, residency selection, cloud escalation, response verification,
fallback chains, routing calibration, or `/v1/route` decision endpoint.
Explicit media workflow profiles do not alter that rule. Measure a concrete serve with
`eval preflight` and benchmark commands before mapping a capability to it; a
configuration edit is not a promotion claim.

## Operational shape

The reference deployment exposes two equivalent RTX PRO 6000 Max-Q cards as
Compute A and Compute B. Split-mode serves reserve either role independently;
one explicitly declared `dual-gpu-exclusive` TP=2 serve may transactionally
reserve both while other GPU inference is offline. The gateway provides one
stable endpoint across those services without pretending capabilities or VRAM
heaps are interchangeable.

See [Configuration](CONFIGURATION.md), [Architecture](ARCHITECTURE.md),
[ADR-0028](adr/0028-serving-benchmarks-and-thin-capability-gateway.md), and
[ADR-0039](adr/0039-capability-meta-router.md), and
[ADR-0040](adr/0040-media-gateway-and-controller-authority.md).
