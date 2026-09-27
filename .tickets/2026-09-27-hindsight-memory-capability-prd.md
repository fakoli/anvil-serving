# Ticket: Hindsight memory capability + multiplexing in the Capability Gateway

Date: 2026-09-27. Status: proposed (pre-implementation PRD; specifies required
behavior and evidence; does not claim implementation or live acceptance).

Source PRD: `ai-infra` `modern/docs/hindsight-prd.md` (F007 / T008). Reference
design: `ai-infra` `modern/docs/hindsight-design.md` (§6–§8). This ticket
covers only the anvil-serving slice; the Hindsight service itself, its
exposure, and its consumers are owned by ai-infra. Per repo policy, host and
bank identities below are synthetic; real declarations live in private
operator state.

## Context

A Hindsight memory service (vectorize-io/hindsight) runs as a hardened
Compose service on the fleet's service host, provisioned by ai-infra in the
Grafana/Open WebUI pattern. Its LLM extraction runs through this repo's
Capability Gateway router (OpenAI-compatible endpoint), and the service is
exposed to one consumer host over Tailscale and Anvil Connect. Two memory
backends then exist in the fleet:

- **hermes** — pi-hermes-memory: local markdown/SQLite FTS stores, curated,
  zero external services, authoritative for pi sessions. There is no network
  API for it today.
- **hindsight** — the new semantic memory service (isolated banks,
  retain/recall/reflect, per-bank MCP endpoints).

Callers should not need to know which backend serves a memory request, and
the fleet must not gain a hidden second brain. The Capability Gateway is the
explicit place for that boundary.

## Proposal

A **memory-capability alias** in the Capability Gateway fronting the two
tiers, plus read-only fleet awareness of the Hindsight service:

1. **Explicit alias, explicit tiers.** Callers use a stable memory
   capability alias; operators map that alias to exactly one tier (hermes or
   hindsight) per declared consumer/bank. Unknown aliases return 404; an
   unavailable selected tier returns an error. No semantic selection, no
   fallback, no substitution, no automatic promotion — the same authority
   model as every other alias (ADR-0042 family boundary).
2. **Principal → allowed-bank enforcement.** The alias binds each
   authenticated principal to its allowed bank(s) and rejects caller-selected
   bank/consumer overrides and unauthorized bank administration. Tailnet
   membership and bank storage isolation do not authorize a caller; the
   exposure path does.
3. **Backend identification.** Responses identify which backend served the
   request (bounded informational context, like model/context facts on chat
   aliases).
4. **Hindsight tier first; hermes tier behind a gated adapter contract.**
   The hindsight tier serves the per-bank MCP surface once the service
   exists. The hermes tier activates only after a separately accepted
   backend-adapter contract exists: a read-only adapter over the existing
   pi-hermes-memory stores (search-style operations only), explicit
   unsupported-operation errors, host/store selection declared per consumer,
   and zero writes to pi-hermes-memory. Until then the alias maps only to
   the hindsight tier.
5. **Alias vs direct access, stated.** Declared consumers (pi, hermes,
   openclaw) connect directly to their bank's Hindsight MCP endpoint for
   hindsight memory; those direct paths are themselves exposure-declared and
   enforce the same principal → bank binding (see the source PRD's
   authorization requirement). The memory alias serves backend-agnostic
   callers and is exercised by this ticket's routed acceptance contract.
6. **Read-only awareness.** Control-plane/topology state carries the
   Hindsight service as a declared integration (read-only). Lifecycle
   ownership stays with ai-infra Ansible; no lifecycle verb for it exists in
   this repo's manifest.
7. **Output-cap reconciliation.** The extraction alias's output budget is
   reconciled and recorded as
   `configured_chunk_size < retain_completion_limit <= effective_output_cap`
   (with compatible context capacity) before extraction goes live.

## Acceptance criteria

- The memory-capability alias resolves exactly one configured tier per
  declared principal; unknown aliases 404; unavailable tiers error without
  trying another backend.
- Responses identify the serving backend.
- Principal → bank binding is enforced on the alias: unauthenticated,
  wrong-principal, and cross-bank read/write attempts fail; caller-selected
  bank overrides and bank-administration calls are rejected.
- While the hermes tier is unadapted, the alias maps only to the hindsight
  tier; hermes-tier selection returns an explicit unsupported-operation
  error, not a silent substitute.
- Control-plane/topology state shows the Hindsight service as read-only
  awareness; no lifecycle verb for it exists in this repo's command
  manifest.
- The extraction alias's output-cap inequality is recorded and holds.
- Boundary review against the family table (ADR-0042) confirms the memory
  capability does not blur Model Serving / Capability Gateway / Control
  Plane ownership; if the family boundary needs amending, that is a separate
  ADR, not a manifest edit.

## Verification

Evidence classes are separate; none substitutes for another:

1. **Endpoint preflight** (existing OpenAI-shaped checks) for the extraction
   alias's endpoint health — proves endpoint liveness only.
2. **Routed extraction-alias acceptance**: a real retain → completion →
   recall round trip through the router alias with representative
   multi-fact content, plus the recorded output-cap inequality. Where the
   existing preflight harness lacks memory-specific checks, that harness
   work is scoped as its own task, not attributed to existing preflight.
3. **Routed memory-alias acceptance**: real client continuations per
   declared principal, backend-identification facts present, and the
   negative tests above (unauthenticated, wrong-principal, cross-bank,
   override rejection).
4. **Boundary review**: family table and command manifest unchanged in
   lifecycle ownership; awareness entries are read-only.

## Out of scope

- Owning the Hindsight service lifecycle (ai-infra Ansible owns it).
- Hidden fallback, semantic routing, or quality-profile routing between
  memory tiers; multi-provider failover inside the memory service.
- LongMemEval benchmark profile (source PRD T009; requires this alias to
  exist first).
- Consumer-side integration wiring (pi/Hermes/OpenClaw clients are
  ai-infra's T006/T007).
- Writing to pi-hermes-memory through the adapter, ever.
