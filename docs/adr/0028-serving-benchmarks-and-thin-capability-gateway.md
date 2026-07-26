# ADR-0028 — Serving, benchmarks, and a thin capability gateway

- **Status:** Accepted
- **Date:** 2026-07-26
- **Relates to:** ADR-0016; ADR-0017; ADR-0018; ADR-0022; ADR-0024; `docs/CONFIGURATION.md`

## Context

The router has accumulated an intelligent-routing path based on intent inference, policy,
quality profiles, residency, and fallback. That path remains valuable compatibility behavior, but
the current operating need is to benchmark and serve concrete local capabilities reliably. The
reference workstation assigns the RTX PRO 6000 to primary LLM candidates and the RTX 5090 to
low-latency voice LLM work, STT/TTS, embeddings, reranking, and on-demand ComfyUI.

Those capabilities still need the router's mature boundary behavior: token authentication,
Anthropic/OpenAI dialect handling, upstream SSE, readiness checks, admission, metadata-only
decision logs, and the existing `serves`, preflight, and benchmark utilities. Removing the legacy
router immediately would risk clients that use presets or rely on its existing behavior before a
compatibility decision is proven.

## Considered options

1. **Keep expanding intelligent routing.** Add more inference, profile, and policy behavior while
   retaining the current product center. This adds complexity without improving the immediate
   capability-serving and benchmark loop.
2. **Remove legacy routing now.** Make every caller use direct model names immediately. This would
   simplify selection but breaks unaccepted compatibility assumptions.
3. **Shift the product center to serving and benchmark evidence, with an additive thin capability
   gateway.** Let exact aliases select a configured tier, keep the legacy router in place for
   unmatched callers, and defer removal until compatibility acceptance.

## Decision

Adopt option 3. anvil-serving's primary framing is local model serving plus benchmark evidence,
with a thin capability gateway around those serves. The gateway preserves auth, dialects, SSE,
readiness, admission, decision logs, `serves`, eval/preflight, and benchmark tooling.

`[router.model_routes]` is the additive direct-routing contract: a normalized alias maps to one
configured tier. Matching is case-insensitive after trimming and removing an optional `anvil/` or
`anvil:` namespace prefix. Aliases cannot shadow preset or tier tokens and can target only local
tiers, so this path cannot bypass the metered-cloud billing gate. A match selects that tier before
the legacy intent, policy, profile, residency, and fallback selection path. Slice 1 deliberately
leaves unmatched `model` values on the legacy path; it does not introduce a strict unknown-model
404.

The reference allocation is RTX PRO 6000 for primary LLM work and RTX 5090 for low-latency voice
LLM, STT/TTS, embeddings, reranking, and on-demand ComfyUI. Embedding/rerank and audio keep their
deterministic purpose/audio routes; ComfyUI remains lifecycle-managed rather than a chat route.
No capability is promoted by this framing or configuration alone.

Freeze new intelligent-routing growth. Do not remove the legacy path until compatibility acceptance
records the affected client behavior, status/error semantics, readiness/admission behavior, and
decision-log expectations.

## Consequences

- New integrations should prefer stable direct capability aliases where a caller knows the desired
  capability; existing preset and classifier callers continue to work in this slice.
- Direct aliases still traverse the gateway's protocol, availability, admission, and audit
  boundaries, but do not invoke legacy route selection or fallback selection.
- Serving changes remain independently preflighted and benchmarked; a checked-in topology example
  is not live qualification or promotion evidence.
- Future removal requires an explicit compatibility acceptance decision and a superseding ADR;
  documentation must not claim that legacy intelligent routing has already been removed.
