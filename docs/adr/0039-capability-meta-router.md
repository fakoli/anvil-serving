# ADR-0039 — Capability meta-router as the product model

- **Status:** Accepted
- **Date:** 2026-08-24
- **Relates to:** ADR-0028 (thin capability gateway); ADR-0033 (plane
  contract); ADR-0038 (inference-owned model metadata)

## Context

ADR-0028 removed the intent classifier, policy ranking, fallback chain, cloud
escalation, and response-verification path. The remaining router is direct: a
caller-visible capability alias maps to exactly one local tier.

ADR-0038 then allowed the selected inference service to own mutable facts such
as its served model identity and context. That prevents router metadata from
becoming stale when an operator replaces a model at an unchanged endpoint.
The feature is dynamic in time but not dynamic in route selection.

Calling the product only a thin gateway accurately describes the request-path
implementation, but it does not name the broader contract among stable caller
capabilities, operator-owned topology and policy, inference-owned runtime
metadata, and human-gated qualification. Conversely, calling it a dynamic
model router would imply selection behavior that the product intentionally
does not have.

## Considered options

### Keep only the thin-gateway framing

This preserves the implementation description but leaves the authority split
implicit. Operators can reasonably mistake inference-owned metadata for a
partial return to dynamic model routing, or continue duplicating mutable serve
facts in router configuration.

### Reintroduce a dynamic model selector

A selector could rank candidates by prompt intent, availability, benchmark
score, or capacity and fall back on failure. This conflicts with the direct
route contract, makes client-visible behavior less predictable, and would
move qualification and promotion decisions into the request path.

### Adopt capability meta-router as the product model (chosen)

Use **capability meta-router** for the product-level contract and retain
**thin capability gateway** for its implementation style. Define meta-routing
as the explicit binding of a stable capability alias to one tier whose
effective served configuration may be reported by the already-selected
inference service.

## Decision

Anvil Serving is a capability meta-router with these ownership boundaries:

- the caller chooses one declared capability alias;
- operator configuration owns the exact alias-to-tier mapping, endpoint,
  authentication reference, dialect, readiness contract, and safety policy;
- the selected inference service may own allowlisted mutable served-model
  facts only when the tier explicitly sets `metadata_source = "upstream"`;
- the router validates and projects the effective served configuration,
  enforces policy and admission, and relays only to the selected endpoint; and
- evaluation evidence plus a human-gated operator transaction own
  qualification and promotion.

The following are invariants, not optional deployment preferences:

1. The chat route vocabulary is closed.
2. Each alias maps to exactly one configured tier.
3. Metadata resolution cannot change the selected tier.
4. Ambiguous, missing, or conflicting required upstream metadata fails closed.
5. A failed request is not retried against another model or endpoint.
6. Router-owned capability and safety policy is not inferred from upstream
   model claims.
7. Readiness, configuration, qualification, promotion, package publication,
   and live deployment remain distinct states.

**2026-09-05 amendment — bounded same-host replicas (design pending implementation).**
A configured tier may eventually declare an explicit 2–16-member equivalent
set on one host. This adds a second, internal decision only after the invariant
alias-to-tier decision: select one eligible member of the already selected tier
with deterministic round robin. It never creates a second tier candidate or
cross-host scheduler. Members must have the same served model and declared
model revision, engine version, image digest, configuration digest, dialect,
context/output/tool/media contract, and host. Health and live served-model-name
checks remain individual; declared provenance is not runtime attestation, and
readiness does not qualify or promote a member.

After a member is selected, the request is attempted once. No later failure may
trigger selection of another member, model, tier, host, or capability. This
amendment grants neither lifecycle authority nor runtime replica support; the
direct single-endpoint behavior remains the implemented contract until the
bounded design is delivered.

Purpose-model and audio routes remain deterministic separate surfaces. This
decision does not add lifecycle authority to the router or change the guarded
serve/promotion operations.

## Consequences

- Product documentation can explain stable aliases and mutable serving facts
  without using the misleading language of intelligent or automatic routing.
- Operators can change the model or context at an upstream-owned single-model
  endpoint without manually duplicating those facts in router settings.
- Clients receive current effective metadata while retaining a stable model
  alias.
- The phrase meta-router must always be paired with the no-selection boundary;
  otherwise readers may assume intent routing or fallback.
- Static tiers remain supported through
  `metadata_source = "configured"`.
- The router stays stdlib-only and thin; the reframe adds no runtime
  dependency, selection algorithm, or control-plane mutation.
- ADR-0028 is not superseded. Its thin direct-gateway decision is the request
  path that implements this product model.
