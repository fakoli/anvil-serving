# Ticket: Hindsight memory capability + multiplexing in the Capability Gateway

Date: 2026-09-27. Status: implemented and independently reviewed; native Pi,
Hermes, OpenClaw and Codex memory continuations pass. Product PR #584 is merged.
Private infrastructure tracks the ongoing curated-memory import and public
Connect publication separately.

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
Capability Gateway router (OpenAI-compatible endpoint). Consumers currently
reach its memory surface through the private router; public Anvil Connect
REST and MCP recall now pass; final infrastructure acceptance remains pending. Two memory
backends then exist in the fleet:

- **hermes** — pi-hermes-memory: local markdown/SQLite FTS stores, curated,
  zero external services, authoritative for pi sessions. There is no network
  API for it today.
- **hindsight** — the new semantic memory service (isolated banks,
  retain/recall/reflect through the principal-bound router MCP endpoint).

Callers should not need to know which backend serves a memory request, and
the fleet must not gain a hidden second brain. The Capability Gateway is the
explicit place for that boundary.

## Proposal

A **memory-capability alias** in the Capability Gateway fronting the two
tiers, plus managed fleet awareness of the Hindsight service. The explicitly
authorized implementation expands the original read-only lifecycle proposal:

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
5. **All consumers use the router.** Pi, Hermes, OpenClaw and Codex use
   `/v1/memory/mcp` with individual device keys and explicit alias grants.
   Native upstream MCP is disabled; its administrative API remains private.
6. **Existing managed lifecycle.** Infrastructure provisions the service,
   then the existing external-Compose `host services` contract adopts it.
   Control-plane operations execute at its declared owner. No new lifecycle
   implementation or product family is introduced.
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
- Control-plane/topology state shows the adopted service, and existing managed
  status/log/lifecycle operations preserve its container and volume ownership.
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
4. **Boundary review**: lifecycle stays in the existing Control Plane family;
   memory routing stays in Capability Gateway. Curated imports use a confirmed
   operator command, with provenance and no source-store writes.

## Verified implementation

- Native Pi completed retain, recall and reflect; Pi on a companion host,
  Hermes, OpenClaw and Codex recalled facts through their configured memory
  tools without provider or native-memory replacement.
- The final product head passed 9,821 local tests (56 skipped), both Linux and
  Windows CI matrices, and independent review. A test-only HTTP teardown fix
  joins request threads before SQLite cleanup on Windows.
- Recall disables upstream entity expansion and returns complete ranked facts
  within a 32 KiB JSON budget, with explicit truncation metadata. MCP sends one
  result representation; recall and reflect advertise read-only annotations.
- Managed service status, logs and restart pass. Recall after restart confirms
  persisted memory, and an isolated nonempty backup restore matches table counts
  and bank defense settings.
- The importer resumes only documents with matching content, provenance and a
  completion marker. Original curated sources remain unchanged. Bulk migration
  completion is an infrastructure acceptance gate, not a router test result.

## Connect publication repair

Live publication exposed two existing product defects. Cloudflare returns ingress
inside `result.config` and reports active connectors as `healthy`; the publisher
previously read a flat ingress field and could discard sibling configuration.
Publication now preserves the complete configuration and verifies readback.

Activating an updated edge certificate also exposed an identity-provider startup
race: the gateway attempted OIDC discovery before the local provider was ready,
then entered its restart interval while the manager's readiness deadline expired.
A guarded recovery restored the running deployment. The durable manager now waits
for verified local issuer discovery before starting or restoring the gateway.
This is a dependency check, not a longer gateway readiness timeout.

Native Connect REST and MCP recall return the expected imported facts through
public DNS and TLS. Resource, authentication and bank-boundary probes accompany
that evidence. The bulk import and its final backup/restore remain separate gates.

## Out of scope

- A new Hindsight-specific lifecycle engine (existing adoption is sufficient).
- Hidden fallback, semantic routing, or quality-profile routing between
  memory tiers; multi-provider failover inside the memory service.
- LongMemEval benchmark profile (source PRD T009; requires this alias to
  exist first).
- Consumer-side integration wiring (pi/Hermes/OpenClaw clients are
  ai-infra's T006/T007).
- Writing to pi-hermes-memory through the adapter, ever.
