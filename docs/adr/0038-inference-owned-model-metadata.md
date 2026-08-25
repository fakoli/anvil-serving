# ADR-0038 — Inference-owned model metadata behind stable capability aliases

- **Status:** Accepted
- **Date:** 2026-08-24
- **Relates to:** ADR-0028 (thin capability gateway); ADR-0032 (public product,
  private operator state); ADR-0033 (durability and plane contract); ADR-0039
  (capability meta-router)

## Context

The router maps a stable caller-visible capability alias to exactly one local
tier. Previously that tier also duplicated the inference service's concrete
model name, context, engine, quantization, and fingerprint. Replacing a model
at an unchanged endpoint therefore required coordinated router edits. When
those edits lagged, discovery and authenticated metadata reported a stale
model or context even though requests reached the new serve.

The inference service already exposes bounded, read-only facts about what it is
serving. OpenAI-compatible
[vLLM](https://docs.vllm.ai/en/stable/api/vllm/entrypoints/openai/models/serving/)
and [SGLang](https://github.com/sgl-project/sglang/issues/8887) model cards can
report model identity and maximum model length.
[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
exposes the model catalog plus `/props`, including its active context and other
descriptive configuration. The router can consume those facts without gaining
lifecycle authority or choosing a different tier.

## Considered options

### Keep all model facts in router configuration

This preserves one static file but creates two authorities for one running
serve. It requires coordinated edits for every model or context replacement
and permits stale but plausible metadata.

### Discover once at router startup

Startup discovery avoids one initial duplicate but freezes the result for the
router process lifetime. It does not support an independently replaced serve
and can retain a stale context until restart.

### Refresh bounded inference-owned metadata (chosen)

Keep the alias, endpoint, authentication, capability policy, and exact tier
mapping configured. Allow the tier to delegate mutable served-model facts to
the single inference service at that endpoint. Refresh them through the
existing readiness cache and fail closed when the response is unavailable,
ambiguous, malformed, or internally inconsistent.

## Decision

Add an explicit `metadata_source = "upstream"` tier mode. It is opt-in and
currently limited to local OpenAI-compatible tiers with a health probe. Such a
tier must omit `model`, `context_limit`, `engine`, `quantization`,
`model_identity`, and configured fingerprint evidence.

Readiness requires a healthy endpoint and exactly one `/v1/models` entry.
Model-card context keys are allowlisted. llama.cpp may additionally supply
`n_ctx`, quantization, build, slots, and modalities through a bounded `/props`
read whose model alias must agree with the catalog. The cached result controls
request context admission, the model id sent to that same endpoint, and
read-only discovery/metadata projections.

The caller's capability alias remains unchanged in request decisions and
responses. The route still resolves to one configured tier, with no retry,
fallback, classifier, or cross-model selection. Router-owned policy such as
tool permission, media admission, output ceilings, and concurrency remains
configured explicitly.

This is the inference-owned metadata mechanism inside the capability
meta-router defined by ADR-0039. It changes the authority for mutable facts
about the selected serve, not the authority for route selection.

## Consequences

- An operator can replace a single-model serve or its context at an unchanged
  endpoint without editing or restarting the router.
- Metadata can remain stale only for the configured readiness-cache interval.
  A request that races an inference replacement may fail; it is not replayed.
- Missing or ambiguous runtime metadata makes the selected tier unavailable
  rather than forwarding a guessed model or admitting against a guessed
  context.
- Static tiers remain byte-compatible under the default
  `metadata_source = "configured"` behavior.
- Serving engines remain responsible for accurately advertising their active
  configuration. Qualification and promotion remain separate human-gated
  operations.
