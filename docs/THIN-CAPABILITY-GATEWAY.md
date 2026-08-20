# Thin capability gateway

anvil-serving exposes measured local capabilities through one authenticated,
OpenAI- and Anthropic-compatible endpoint. It does not infer workload intent,
rank candidate models, apply quality profiles, or retry another model.

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

The chosen tier remains subject to its declared context and tool capabilities,
health/readiness probes, and concurrency admission. If it is unavailable or
admission is exhausted, the gateway returns its configured exhaustion response;
it does not select a substitute tier.

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

Additional authenticated read-only surfaces expose model capabilities,
fingerprints, router status, current-buffer statistics, request traces, and
Prometheus gauges. They project existing configuration/readiness/decision
metadata and do not add model selection or lifecycle authority.

Purpose models are deterministic model-name routes on `/v1/embeddings` and
`/v1/rerank`. Audio routes are deterministic operator-selected STT/TTS routes
under `/v1/audio/*`. ComfyUI is lifecycle-managed separately and is not a chat
model route.

## Router observability API

The router exposes authenticated, read-only operational views over its explicit
model configuration, readiness state, bounded decision log, and serving-engine
metrics. Except for `GET /healthz`, the normal router bearer token or
`x-api-key` is required. Operational responses use
`Cache-Control: no-store`.

| Endpoint | Purpose |
|---|---|
| `GET /v1/models/capacity` | Declared GPU/model capacity, bounded live engine signals, and request-scenario arithmetic. |
| `GET /v1/models/capabilities` | Tools, modalities, thinking behavior, context, and multimodal limits. |
| `GET /v1/models/fingerprints` | Declared checkpoint/engine identity plus identity observed by readiness. |
| `GET /v1/router/status` | Package version, uptime, aliases, tier counts, and a secret-free effective-config hash. |
| `GET /v1/stats` | Tokens, bytes, outcomes, and latency percentiles over the current decision-log buffer. |
| `GET /v1/requests/{request_id}` | Metadata-only trace for an exact sanitized request identifier. |
| `GET /metrics` | Prometheus gauges for router-buffer aggregates and current model capacity/load. |

Model endpoints and `/v1/stats` accept `model=<configured-alias>`. Capacity also
accepts `gpu_role`, `images`, `input_tokens`, `image_tokens`, and
`output_tokens`. Stats and Prometheus accept `limit` from 1 through 10,000.
Unknown aliases return 404; unsupported or malformed parameters return 400.

The decision log is a bounded in-memory ring buffer with no record timestamps.
Consequently, `/v1/stats` reports
`scope = "current_decision_log_buffer"` instead of accepting a historical
window. Prometheus values are gauges because eviction or restart can reduce
them. Live engine-metric failures make those values unavailable; they do not
fail routing or expose raw upstream errors.

Responses omit prompts, responses, message content, audio, transcripts,
upstream URLs, host identifiers, auth environment names, credentials, and
arbitrary tier parameters. Lifecycle and model promotion remain behind the
serving/controller operations; no read-only endpoint can mutate them.

## What is intentionally absent

There are no intent presets, classifier, policy/profile selection, residency
selection, cloud escalation, response verification, fallback chains, routing
calibration, or `/v1/route` decision endpoint. Measure a concrete serve with
`eval preflight` and benchmark commands before mapping a capability to it; a
configuration edit is not a promotion claim.

## Operational shape

The reference deployment exposes two equivalent RTX PRO 6000 Max-Q cards as
Compute A and Compute B. Split-mode serves reserve either role independently;
one explicitly declared `dual-gpu-exclusive` TP=2 serve may transactionally
reserve both while other GPU inference is offline. The gateway provides one
stable endpoint across those services without pretending capabilities or VRAM
heaps are interchangeable.

See [Configuration](CONFIGURATION.md), [Architecture](ARCHITECTURE.md), and
[ADR-0028](adr/0028-serving-benchmarks-and-thin-capability-gateway.md).
