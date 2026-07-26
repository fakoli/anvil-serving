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
llm.primary = "heavy-local"
llm.voice = "fast-local"
vision.ocr = "ocr-local"
vision.general = "vision-local"
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

Purpose models are deterministic model-name routes on `/v1/embeddings` and
`/v1/rerank`. Audio routes are deterministic operator-selected STT/TTS routes
under `/v1/audio/*`. ComfyUI is lifecycle-managed separately and is not a chat
model route.

## What is intentionally absent

There are no intent presets, classifier, policy/profile selection, residency
selection, cloud escalation, response verification, fallback chains, routing
calibration, or `/v1/route` decision endpoint. Measure a concrete serve with
`eval preflight` and benchmark commands before mapping a capability to it; a
configuration edit is not a promotion claim.

## Operational shape

The reference deployment puts primary LLM serving on the RTX PRO 6000. The RTX
5090 serves the low-latency voice LLM, STT/TTS, embeddings, reranking, and
on-demand ComfyUI. The gateway provides one stable endpoint across those
separate services without pretending they are interchangeable.

See [Configuration](CONFIGURATION.md), [Architecture](ARCHITECTURE.md), and
[ADR-0028](adr/0028-serving-benchmarks-and-thin-capability-gateway.md).
