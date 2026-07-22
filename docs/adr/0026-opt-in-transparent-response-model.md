# ADR-0026 — Opt-in transparent response model reports the served tier

- **Status:** Accepted
- **Date:** 2026-07-22
- **Relates to:** Issue #180; `docs/QUALITY-GATED-ROUTER.md` §9; ADR-0013

## Context

The router accepts intent-oriented model tokens such as `chat`, `planning`, and `quick-edit`, then
selects a quality-approved tier. Until this decision, every successful wire response copied that
requested token into `model`, even when verification fallback selected a different tier. The
decision log recorded the real tier server-side, but a client could not learn it from the response.

Changing `model` unconditionally is incompatible with harnesses that validate the response model
against the requested token. The contract must also stay consistent across Chat Completions,
Anthropic Messages, and Responses, in buffered and streaming paths, without changing the concrete
model id sent to the selected upstream.

## Considered options

1. Always report the served tier. This is maximally transparent but breaks strict clients.
2. Keep identity server-side only. This preserves compatibility but leaves the documented
   transparency goal unimplemented.
3. Add a default-off router option that reports the served tier across every chat dialect.

## Decision

Add `[router].transparent_response_model`, a validated boolean that defaults to `false`. When it is
`true`, `RoutingBackend` retains the winning tier in request-thread-local state. The production
server explicitly injects only that routing-owned resolver into the front door, which passes the
resolved value to every shipped chat dialect for all wire objects and stream bookends. Plain
`make_server` and non-routing/plugin backends receive no such capability and always retain the
requested model token, regardless of request mutation.

The response value is the stable Anvil tier id, not the provider's concrete upstream model id.
Upstream dispatch remains unchanged and continues to use the selected tier's configured `model`
(or the existing request fallback when it is unset).

## Consequences

- Compatibility is unchanged for existing configurations.
- Operators who need client-visible routing identity can enable one cross-dialect option.
- Enabling it intentionally discloses configured tier ids to callers able to reach the router
  (authenticated when front-door auth is configured); tier ids must not contain secrets or
  sensitive internal labels.
- A verified fallback reports the winning tier rather than the first attempted tier.
- Streaming opening and terminal objects use the same served-tier value.
- The request `model` is never rewritten, and upstream dispatch remains independent of response
  identity.
- Resolver failure or an invalid resolver result falls back to the requested model rather than
  failing an otherwise successful inference.
- Exhausted requests have no successful response model and keep their existing error envelope.
