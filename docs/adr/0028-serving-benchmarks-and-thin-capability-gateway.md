# ADR-0028 — Serving, benchmarks, and a thin capability gateway

- **Status:** Accepted
- **Date:** 2026-07-26
- **Relates to:** ADR-0016; ADR-0017; ADR-0018; ADR-0022; ADR-0024

## Context

The project now concentrates on repeatable model serving and benchmark evidence.
Its router had accumulated inferred intent, profile and policy ranking,
residency selection, verification, and fallback behavior. Those mechanisms made
a caller's selected capability ambiguous and consumed code without serving the
current operating model.

The reference deployment has one primary LLM serving location: the RTX PRO
6000. The RTX 5090 remains active for a low-latency voice LLM, STT/TTS,
embeddings, reranking, and on-demand ComfyUI. These are distinct capabilities,
not interchangeable chat candidates.

## Decision

anvil-serving is a serving and benchmark substrate with a direct capability
gateway. `[router.model_routes]` is required and is the complete chat `model`
vocabulary. A normalized alias maps to exactly one configured local tier.
Matching is case-insensitive after trimming. Compatibility prefixes are not
accepted. Unknown or absent aliases return 404.

The selected tier still uses token authentication, protocol and tool
translation, true upstream SSE, readiness checks, admission control, and
metadata-only decision records. An unavailable or overloaded selected tier
returns the configured exhaustion error; the gateway does not select another
tier.

Embedding/rerank routes remain deterministic by purpose-model name. Audio
routes remain deterministic and operator-owned. ComfyUI remains a managed
lifecycle capability rather than a chat route.

Remove the old intent classifier, presets, routing policy and profiles,
residency selection, response verification, fallback and commit-window chain,
cloud routing, route calibration, and `/v1/route` surface. This is a deliberate
breaking change: compatibility with the removed vocabulary is not required.

## Consequences

- Callers must send a configured alias such as `llm.primary` or `llm.voice`.
- The public model list is the configured aliases, not a synthetic intent
  vocabulary.
- Failure behavior is explicit and auditable; it no longer hides a capability
  failure by substituting another model.
- Benchmark and preflight evidence, not router history, govern changes to a
  route mapping.
- Existing historical ADRs remain historical records; their superseded routing
  mechanisms do not define the current runtime contract.
