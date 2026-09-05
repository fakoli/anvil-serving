# Project: Qualified Same-Host Replica Sets

## Summary

Extend the direct-only Anvil Serving chat router so one configured logical tier can contain multiple explicitly declared, independently ready, equivalently qualified endpoints on the same host. A caller alias still resolves exactly once to one logical tier; the router may select only an eligible member of that tier and must never substitute another model, capability, host, or unqualified endpoint. This is the prerequisite for turning already measured dual-replica throughput into a supported routing contract without becoming a cross-host scheduler.

## Goals

- Represent two or more same-host endpoints as one closed, explicitly configured replica set behind one existing chat alias.
- Require exact model and deployment identity plus a durable qualification reference for every member before it becomes eligible.
- Probe, admit, select, drain, and observe replica members independently while preserving tier-wide policy.
- Preserve the current direct-tier behavior and wire protocol for every configuration that does not declare replicas.
- Leave implementation units, file seams, invariants, and verification commands precise enough for a bounded execution model to implement one task at a time.

## Non-Goals

- Cross-host request scheduling, automatic host discovery, or failover to another topology node.
- Prompt classification, quality routing, cloud routing, tier fallback, model substitution, or capability substitution.
- Retrying, replaying, or hedging a request after any upstream backend has been invoked.
- Starting, stopping, repairing, qualifying, promoting, or automatically enrolling replica processes.
- Treating a successful health check, configuration entry, benchmark, or readiness probe as qualification or promotion.
- Selecting members from serving-engine names, quantization formats, GPU models, or vendor-specific runtime behavior.
- Adding a runtime dependency to the stdlib-only router.

## Requirements

- R001: Each normalized entry in `[router.model_routes]` must continue to map to exactly one logical tier, and unknown aliases must continue to return the existing not-found response.
- R002: A chat tier must declare exactly one endpoint shape: the existing direct `base_url` form or a new `replicas` collection containing at least two members; mixed, empty, or singleton replica declarations must fail configuration loading.
- R003: Every replica member must declare a unique bounded `id`, a `base_url`, the existing environment-backed authentication reference when required, a health path, one topology host identifier, and one non-empty qualification reference.
- R004: All members of one replica tier must declare the same topology host identifier, and managed configuration validation must cross-check that host against the tier's topology-owned resource before any router deployment is rendered.
- R005: A replica tier must use upstream-owned runtime metadata, enable exact model identity, and declare non-empty expected `model`, `model_revision`, `engine_version`, `image_digest`, and `config_fingerprint` values shared by every member.
- R006: A qualification reference is metadata that identifies durable evidence; the router must validate only that it is present and safe to expose, and must not claim that the referenced evidence exists, passed, or authorizes promotion.
- R007: Readiness and runtime identity must be evaluated independently for every member by reusing the existing bounded health and `/v1/models` metadata probes; one failed member must not make another conforming member unavailable.
- R008: The first release must select among currently eligible members with deterministic rotating round robin; if no member is eligible, it must return the existing exhaustion behavior without consulting another tier or host.
- R009: Selection must occur once after request capability, context, tool, media, output, readiness, and tier admission checks; after the selected backend is invoked, any timeout, disconnect, protocol error, or upstream error must be returned without selecting a second member.
- R010: Existing tier-level context, tool, media, output, auth, timeout, metadata, and admission policies must apply identically to every member; replica configuration must not create member-specific semantic policy.
- R011: Tier quiesce must stop new leases for the full replica set, tier drain must wait for all member leases, and member readiness loss must stop new selection without cancelling an in-flight request.
- R012: Decision, status, and metadata projections must identify the logical tier and selected member ID, readiness reason, and expected-versus-observed identity while excluding endpoint URLs, topology addresses, auth material, prompts, response content, and raw exceptions.
- R013: Existing direct tiers, purpose routes, audio routes, discovery, wire translation, true SSE streaming, authentication, and response normalization must remain behaviorally compatible.
- R014: The implementation must remain stdlib-only inside `anvil_serving/`, use immutable dataclasses or equivalent bounded value objects at configuration boundaries, return structured values from library code, and keep printing in CLI wrappers.

## Acceptance Criteria

- A valid two-member same-host replica fixture loads, exposes one alias and one logical tier, independently marks member readiness, and distributes four sequential requests in the stable order member-a, member-b, member-a, member-b.
- Config loading refuses mixed direct/replica shapes, duplicate member IDs, fewer than two members, cross-host members, missing qualification references, missing exact identity fields, and unsafe member identifiers with deterministic `ConfigError` messages.
- When one member is unavailable before selection, new requests use only the other member; when both are unavailable, the route returns the existing exhaustion response and records no attempted fallback.
- A selected member that returns 500, times out, disconnects, or emits malformed SSE is attempted exactly once; tests prove the peer member receives no replay.
- Quiesce and drain tests prove aggregate tier counts equal the sum of member leases and that direct-tier transition behavior remains unchanged.
- Router metadata and decision-log tests prove member identity is visible but URLs, host addresses, credentials, payloads, and raw errors are absent.
- The router-focused and full repository gates pass from a clean public worktree.

## Risks

- Conflating a logical tier with its physical members could leak endpoint identity into public status or accidentally weaken the one-alias/one-tier invariant.
- A readiness snapshot can change between selection and dispatch; the contract therefore guarantees one bounded attempt, not transparent success.
- Reusing one tier admission ceiling across multiple members can underutilize replicas until the follow-on capacity scheduler adds member reservations.
- Existing direct-tier assumptions may be duplicated in config, readiness, metadata, discovery, and transition code; all projections must be updated together.
- Qualification references can become stale; they are provenance labels only and cannot replace a promotion or live acceptance gate.

## Open Questions

None. The first release deliberately uses same-host membership, shared tier policy, upstream-owned exact identity, deterministic round robin, and no post-dispatch retry.

## Assumptions

### A001: Replica membership is a property of one logical tier, not a second routing layer.

**Rationale:** This preserves the shipped direct-only contract in `docs/ARCHITECTURE.md`, `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`, and `anvil_serving/router/serve.py`: an alias resolves once, while member selection is bounded inside the selected tier.

**Requirements:** R001, R008, R009, R013

### A002: Exact expected identity is identical across members in the first release.

**Rationale:** Requiring one model/revision/runtime/config fingerprint makes equivalence testable and prevents a replica set from becoming hidden model fallback. Heterogeneous-but-equivalent deployments require a separate reviewed contract.

**Requirements:** R005, R006, R007

### A003: Managed topology validation is authoritative for same-host ownership.

**Rationale:** The router should not learn real topology or compare network addresses at request time. Render-time validation can use the declared public/private topology boundary while the runtime receives only a validated member set.

**Requirements:** R003, R004, R012

## Code Map

- `anvil_serving/router/config.py::Tier`, `RouterConfig`, `_parse_tier`, and `load` own immutable router configuration and the complete alias vocabulary. Add member types and closed-shape validation here; do not infer replicas from URLs or model names.
- `anvil_serving/router/serve.py::build_backends`, `_ConcurrencyLimitedBackend`, and `RoutingBackend` own backend construction and the single resolve/check/relay path. Keep alias resolution outside member selection and preserve the no-fallback dispatch boundary.
- `anvil_serving/router/availability.py::RuntimeModelMetadata`, `AvailabilityResult`, and `HttpHealthAvailability` own bounded health and exact runtime identity. Introduce member-keyed probes by composition rather than vendor-specific branches.
- `anvil_serving/router/admission.py::AdmissionSnapshot`, `AdmissionLease`, and `TierAdmission` own quiesce/drain semantics. This PRD needs aggregate member reporting but does not yet replace the tier ceiling with capacity scoring.
- `anvil_serving/router/model_metadata.py`, `model_capacity.py`, `discovery.py`, and `decision_log.py::DecisionLog` own metadata-only projections. Add safe member IDs and state without endpoint or topology identity.
- `anvil_serving/serves.py` owns managed render, validation, transition, and promotion checks. Cross-check topology ownership there rather than teaching the request router fleet topology.
- `tests/router/test_config.py`, `tests/router/test_availability.py`, `tests/router/test_backends.py`, `tests/router/test_admission.py`, `tests/router/test_model_routes.py`, `tests/router/test_model_metadata.py`, `tests/router/test_decision_log.py`, `tests/router/test_streaming_relay.py`, and `tests/router/test_transition_integration.py` are the primary regression seams.
- `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`, `docs/adr/0039-capability-meta-router.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/THIN-CAPABILITY-GATEWAY.md` must agree on the bounded same-host exception.

## Features

### F001: Closed replica configuration contract

Define an explicit same-host member collection, exact shared identity, and render-time topology validation while preserving direct tiers.

**Requirements:** R001, R002, R003, R004, R005, R006, R010, R014

### F002: Independent member readiness and backend construction

Construct and probe each declared member independently without changing tier semantics or adding engine-aware routing.

**Requirements:** R005, R007, R010, R013, R014

### F003: Single-attempt member routing and lifecycle

Select one eligible member with deterministic rotation and retain the current no-replay, quiesce, drain, and exhaustion behavior.

**Requirements:** R008, R009, R011, R013

### F004: Safe replica observability and documentation

Expose member-level state and decisions as bounded metadata and synchronize the architecture, configuration, and operator contracts.

**Requirements:** R006, R012, R013

## Tasks

### T001: Amend the routing decision record for bounded same-host replicas

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md, docs/adr/0039-capability-meta-router.md, docs/ARCHITECTURE.md

Document that the alias-to-tier decision remains singular and that replica selection is an internal same-host implementation detail. State the exact exclusions: no cross-host scheduler, hidden substitution, lifecycle authority, or post-dispatch replay. Executor guidance: quote existing invariants before editing, make the smallest amendment that removes the apparent conflict, and do not revise historical claims unrelated to replica routing.

**Acceptance criteria:**

- The ADRs and architecture define alias resolution and member selection as two separate decisions.
- The same-host and exact-equivalence constraints are normative.
- The no-retry boundary and qualification-versus-readiness distinction are explicit.

**Verification:**

- `python scripts/check_markdown_links.py --root .`
- `python -m mkdocs build --strict`
- `git diff --check`

### T002: Add replica member types and fail-closed configuration parsing

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/config.py, tests/router/test_config.py, tests/router/fixtures/single-tier-local.toml
**Dependencies:** T001

Add an immutable `ReplicaMember` value object and a closed endpoint union on `Tier`. Preserve existing `tier.base_url` behavior for direct tiers. Parse only an explicit `replicas` array/table shape, normalize no IDs beyond the existing safe identifier policy, aggregate all deterministic validation failures, and return frozen tuples/mappings. Executor guidance: follow `_parse_tier` field validation and `ConfigError` wording patterns; do not use truthiness to distinguish a configured empty value from absence; add focused positive and one-error-per-invariant fixtures.

**Acceptance criteria:**

- Valid direct and replica tiers load into immutable types.
- Every invalid shape in R002-R005 fails before a `RouterConfig` is returned.
- Existing direct fixtures produce equal effective configuration and all old config tests pass.

**Verification:**

- `python scripts/run_tests.py tests/router/test_config.py -x -q`
- `python -m ruff check anvil_serving/router/config.py tests/router/test_config.py`

### T003: Build member backends and independent readiness probes

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/backends/relay.py, anvil_serving/router/backends/sse.py, anvil_serving/router/availability.py, tests/router/test_backends.py, tests/router/test_availability.py
**Dependencies:** T002

Build one existing HTTP/backend adapter and one availability probe per member, keyed by the declared safe member ID. Reuse dialect, timeout, auth-env, bounded body, and metadata comparison code. Keep serving-engine details in upstream metadata and manifests. Executor guidance: prefer a small member aggregate around existing objects over parallel implementations; inject clocks/transports in tests; never log the member URL or auth lookup result.

**Acceptance criteria:**

- Each member can independently be ready, unavailable, identity-mismatched, or probe-failed.
- Runtime identity must match every expected field before the member is eligible.
- Direct tiers still construct exactly one existing backend and probe.

**Verification:**

- `python scripts/run_tests.py tests/router/test_backends.py tests/router/test_availability.py -x -q`
- `python scripts/run_tests.py tests/router/test_dynamic_upstream_metadata.py -x -q`

### T004: Add deterministic round-robin selection with one dispatch attempt

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/admission.py, tests/router/test_model_routes.py, tests/router/test_admission.py, tests/router/test_streaming_relay.py
**Dependencies:** T003

Add a lock-protected rotating cursor scoped to each replica tier. Compute the eligible member snapshot before dispatch, choose once, and pass the selected backend through the existing relay. Preserve tier admission as the authority in this PRD and expose aggregate/member lease counts needed for drain. Executor guidance: keep the selection function pure except for cursor advancement, acquire/release leases with `try/finally`, and add fakes that count backend invocations so no-replay is proven rather than inferred from response status.

**Acceptance criteria:**

- Sequential and concurrent tests show stable fair rotation across eligible members.
- An unavailable member is skipped before dispatch and re-enters only after readiness recovers.
- Every post-dispatch failure invokes exactly one backend.
- Tier quiesce and drain include every member lease without cancelling in-flight work.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_routes.py tests/router/test_admission.py tests/router/test_streaming_relay.py -x -q`
- `python scripts/run_tests.py tests/router/test_transition_integration.py -x -q`

### T005: Extend safe metadata, capacity, discovery, and decision projections

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/model_metadata.py, anvil_serving/router/model_capacity.py, anvil_serving/router/discovery.py, anvil_serving/router/decision_log.py, tests/router/test_model_metadata.py, tests/router/test_model_capacity.py, tests/router/test_discovery.py, tests/router/test_decision_log.py
**Dependencies:** T003, T004

Project one logical tier with a bounded ordered member list. Record selected member ID and pre-dispatch eligibility/identity reason while retaining the existing content-free `DecisionLog` contract and bounded ring behavior. Executor guidance: build allowlisted dictionaries explicitly, keep `base_url`, host address, auth environment/value, request fields, and exception text out, and add negative assertions for every prohibited field.

**Acceptance criteria:**

- Metadata and discovery preserve one public alias and one logical tier.
- Capacity shows aggregate tier admission plus per-member readiness without implying aggregate qualified throughput.
- Decision records identify the selected member and exactly one attempt.
- Redaction tests prove prohibited data cannot appear in serialized output.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py tests/router/test_discovery.py tests/router/test_decision_log.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`

### T006: Validate managed rendering, document the schema, and run release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/serves.py, docs/CONFIGURATION.md, docs/THIN-CAPABILITY-GATEWAY.md, docs/CLI.md, tests/test_serves_ensure_router.py, tests/router/test_serve_cli.py
**Dependencies:** T002, T003, T004, T005

Add render-time same-host/topology-owner checks and document a fully generic two-member example using `127.0.0.1` endpoints and non-capability-bearing qualification references. Do not add real topology or imply deployment. Executor guidance: update generated command/document manifests through the repository's existing generator, not by hand, and keep the example byte-compatible with any packaged scaffold if it is added there.

**Acceptance criteria:**

- Managed render refuses host-owner mismatches before service mutation.
- Documentation explains direct compatibility, exact identity, round robin, no replay, and the later capacity-scheduler boundary.
- No example contains a real address, host identity, credential, or promotion claim.
- Router, full test, lint, strict-doc, link, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/test_serves_ensure_router.py -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `git diff --check`
