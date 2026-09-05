# Project: Qualified Same-Host Replica Sets

## Summary

Extend the direct-only Anvil Serving chat router so one configured logical tier can contain multiple explicitly declared, independently ready, equivalently qualified endpoints on the same host. A caller alias still resolves exactly once to one logical tier; the router may select only an eligible member of that tier and must never substitute another model, capability, host, or unqualified endpoint. This is the prerequisite for turning already measured dual-replica throughput into a supported routing contract without becoming a cross-host scheduler.

## Goals

- Represent two or more same-host endpoints as one closed, explicitly configured replica set behind one existing chat alias.
- Require a live exact served-model check, shared declared deployment identity, and a durable qualification reference for every eligible member.
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
- R003: Each replica member declares a tier-local unique `id`, unique `base_url`, topology `host_id`, unique `resource_id`, and opaque `qualification_ref`. Membership is bounded to 2-16 members; auth, health path, timeout, dialect, model, context, and semantic policy remain shared at tier scope.
- R004: Before managed rendering or router-config activation, every member resource must resolve in validated topology to the declared host and a non-null endpoint normalized-equal to its URL. All member hosts must match. Validation performs no DNS lookup or address-based ownership inference.
- R005: Replica tiers require `metadata_source = "configured"`, `model_identity = true`, one served `model`, and immutable shared `replica_identity` fields `model_revision`, `engine_version`, `image_digest`, and `config_fingerprint`. These are operator-declared deployment provenance; live probes attest only health and served model identity, not revision/image/configuration bytes.
- R006: A qualification reference is metadata that identifies durable evidence; the router must validate only that it is present and safe to expose, and must not claim that the referenced evidence exists, passed, or authorizes promotion.
- R007: Independently probe each member through bounded health and `/v1/models` checks. Eligibility requires the expected served model and health plus the declared provenance contract. Cache/backend keys are composite `(tier_id, member_id)`; equal member IDs in different tiers must never share state.
- R008: The first release must select among currently eligible members with deterministic rotating round robin; if no member is eligible, it must return the existing exhaustion behavior without consulting another tier or host.
- R009: Complete request semantic checks and member readiness snapshots first. Under one tier-owned condition, recheck tier quiesce, select once, advance the cursor, and acquire one compound tier/member lease. Any later failure must be returned without selecting a second member.
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

None. The first release uses same-host membership, shared configured policy, declared deployment provenance, live served-model verification, deterministic round robin, and no post-dispatch retry.

## Assumptions

### A001: Replica membership is a property of one logical tier, not a second routing layer.

**Rationale:** This preserves the shipped direct-only contract in `docs/ARCHITECTURE.md`, `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`, and `anvil_serving/router/serve.py`: an alias resolves once, while member selection is bounded inside the selected tier.

**Requirements:** R001, R008, R009, R013

### A002: Exact expected identity is identical across members in the first release.

**Rationale:** One declared model/revision/runtime/config fingerprint defines intended equivalence. Current probes verify only served model identity. Operator-controlled configuration and qualification remain the trust boundary; heterogeneous deployments or runtime attestation need a separate contract.

**Requirements:** R005, R006, R007

### A003: Managed topology validation is authoritative for same-host ownership.

**Rationale:** The router should not learn real topology or compare network addresses at request time. Render-time validation can use the declared public/private topology boundary while the runtime receives only a validated member set.

**Requirements:** R003, R004, R012

## Closed v1 implementation contract

- Members contain exactly `id`, `base_url`, `host_id`, `resource_id`, and `qualification_ref`. The capacity follow-on may add `max_concurrency`; unknown keys fail closed. The tier keeps shared auth, health path, timeout, dialect, model, context, output, tool and media policy.
- Member/host/resource IDs use `[A-Za-z][A-Za-z0-9_-]{0,63}`. Qualification references use `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and are opaque labels, never paths or URLs. Image/config digests require `sha256:` plus 64 lowercase hex characters; model revision and engine version use `[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}` (1-128 ASCII characters).
- Preserve the direct `Tier.base_url` field; replica tiers use an empty internal sentinel that must never reach a URL builder. Derive per-member direct-tier views with `dataclasses.replace` only after closed-shape validation. Preserve the outer backend map keyed by logical tier; nest members in a tier-owned aggregate.
- Probe/cache/admission keys are composite identities, not concatenated ambiguous strings. Invalidating a logical tier invalidates all its member generations. Stale in-flight probes must not repopulate an invalidated generation; add an event-controlled invalidation race test.
- Round-robin starts at the lexically smallest eligible member ID, rotates only on successful reservation, and skips unavailable members. Request semantic gates happen before one atomic tier/member lease acquisition. No second backend is invoked after selection, including failures before the first response byte.
- Observability reports `deployment_identity_source = "declared"` and `runtime_deployment_identity_verified = false`. Never echo an unexpected upstream model string; expose a fixed mismatch code, with observed model populated only when it equals the validated expected model.
- Managed validation joins resource IDs and normalized endpoint URLs offline. Router lifecycle shortcuts that require one backing serve explicitly refuse replica tiers before mutation; this release does not broaden model lifecycle authority.
- Negative controls must cover identical member names in two tiers, duplicate endpoints, stale probe publication, missing topology resource, wrong resource endpoint/host, unknown identity fields, quiesce/select races, and closing a stream before its first iteration.

## Code Map

- `anvil_serving/router/config.py::Tier`, `RouterConfig`, `_parse_tier`, and `load` own immutable router configuration and the complete alias vocabulary. Add member types and closed-shape validation here; do not infer replicas from URLs or model names.
- `anvil_serving/router/serve.py::build_backends`, `_ConcurrencyLimitedBackend`, and `RoutingBackend` own backend construction and the single resolve/check/relay path. Keep alias resolution outside member selection and preserve the no-fallback dispatch boundary.
- `anvil_serving/router/availability.py::RuntimeModelMetadata`, `AvailabilityResult`, and `HttpHealthAvailability` own bounded health and served-model identity. Introduce member-keyed probes by composition rather than vendor-specific branches.
- `anvil_serving/router/admission.py::AdmissionSnapshot`, `AdmissionLease`, and `TierAdmission` own quiesce/drain semantics. This PRD needs aggregate member reporting but does not yet replace the tier ceiling with capacity scoring.
- `anvil_serving/router/model_metadata.py`, `model_capacity.py`, `discovery.py`, and `decision_log.py::DecisionLog` own metadata-only projections. Add safe member IDs and state without endpoint or topology identity.
- `anvil_serving/deploy.py` owns managed rendering (`serves render` delegates to it), with `tests/test_deploy.py`. `anvil_serving/serves.py` owns router-config activation and transition/promotion checks. Add pure topology validation at those boundaries, never inside request selection.
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

### T003: Add independent member readiness probes

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/availability.py, tests/router/test_availability.py, tests/router/test_dynamic_upstream_metadata.py
**Dependencies:** T002

Probe every declared member under composite `(tier_id, member_id)` identity while reusing the tier health path, timeout, bounded body, and served-model comparison. Make cache invalidation generation-fenced so a stale in-flight probe cannot publish after tier invalidation. Inject clocks and transports in tests; never log endpoint URLs, unexpected upstream model strings, or auth lookups.

**Acceptance criteria:**

- Each member can independently be ready, unavailable, identity-mismatched, or probe-failed.
- Live served-model identity must match; revision/image/configuration remain declared, explicitly unverified runtime provenance.
- Identical member IDs in different tiers never share cache, lock, result, or invalidation state.
- An event-controlled race proves a stale probe cannot repopulate an invalidated tier generation.
- Direct tiers still use the existing single-tier probe behavior.

**Verification:**

- `python scripts/run_tests.py tests/router/test_availability.py -x -q`
- `python scripts/run_tests.py tests/router/test_dynamic_upstream_metadata.py -x -q`

### T004: Add atomic round-robin member admission

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/admission.py, tests/router/test_admission.py, tests/router/test_model_routes.py
**Dependencies:** T003

Add a rotating cursor and member counters scoped to each replica tier. Accept already-completed readiness snapshots; under the tier-owned condition recheck tier quiesce, choose one eligible member, advance the cursor, and acquire the compound tier/member lease atomically. Expose reconciled aggregate/member counts and keep the ranking function pure. Use barriers/events for acquire-versus-quiesce and concurrent rotation tests, never sleeps.

**Acceptance criteria:**

- Sequential and concurrent tests show stable fair rotation across eligible members.
- An unavailable member is skipped before dispatch and re-enters only after readiness recovers.
- Tier quiesce and drain include every member lease without cancelling in-flight work.
- Failed admission creates no tier or member reservation, and release is exactly once.
- Tier active count equals the sum of member active counts after every completed test phase.

**Verification:**

- `python scripts/run_tests.py tests/router/test_admission.py tests/router/test_model_routes.py -x -q`
- `python scripts/run_tests.py tests/router/test_transition_integration.py -x -q`

### T005: Extend safe model and capacity projections

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/model_metadata.py, anvil_serving/router/model_capacity.py, anvil_serving/router/serve.py, tests/router/test_model_metadata.py, tests/router/test_model_capacity.py
**Dependencies:** T003, T004

Project one logical tier with a bounded ordered member list, declared deployment-identity source, runtime-verification flag, aggregate admission, and per-member readiness. Build allowlisted dictionaries explicitly and keep `base_url`, host address, auth environment/value, request fields, unexpected model strings, and exception text out.

Wire `RoutingBackend.model_capacity()` to pass its actual admission owner to the
projection. Its default owner must be initialized with configured replica
membership, while an explicitly injected owner is preserved. No request dispatch
or member-selection behavior is added in this task. Read the owner only once per
tier; absent, invalid or inconsistent state is unavailable, never zero load.
Draining implies quiesced, and aggregate/member counts must be exact integers
in `0..2^53-1`. Ineligible member readiness must not retain a success reason.
Do not fetch metrics through the replica-tier empty URL sentinel or infer
aggregate KV capacity from per-member estimates.

**Acceptance criteria:**

- Metadata preserves one public alias and one logical tier.
- Capacity shows aggregate tier admission plus per-member readiness without implying aggregate qualified throughput.
- A real authenticated HTTP capacity request through `RoutingBackend` shows the same active, quiesced and released member counts as its injected owner; default construction reports complete zero member counts. A missing or forged owner remains unavailable.
- Declared deployment provenance is clearly separated from live served-model verification.
- Redaction tests prove prohibited data cannot appear in either projection.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/model_metadata.py anvil_serving/router/model_capacity.py anvil_serving/router/serve.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py`

### T006: Validate replica topology during managed rendering

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/deploy.py, tests/test_deploy.py
**Dependencies:** T002

Add pure topology/member validation to managed rendering before mutation. Join each member's `resource_id` to validated topology, require exact declared host ownership and normalized endpoint equality, require one shared host, and perform no DNS or address inference. Preserve direct-tier rendering byte-for-byte where feasible.

**Acceptance criteria:**

- Managed render refuses host-owner mismatches before service mutation.
- Missing resources, endpoint mismatches, duplicate resources, and cross-host sets fail deterministically.
- Valid generic replica topology renders one closed router tier without claiming runtime attestation.
- Direct-tier render tests remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_deploy.py -x -q`
- `python -m ruff check anvil_serving/deploy.py tests/test_deploy.py`

### T007: Build replica runtime member backends

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, tests/router/test_backends.py
**Dependencies:** T003

Build existing member adapters under a `ReplicaRuntime` aggregate stored at the outer logical tier ID. Derive validated per-member direct-tier views only after configuration loading, preserve the tier-wide dialect, timeout, auth, and semantic policy, and keep the outer backend map keyed by tier ID. Do not create a second alias-resolution or engine-selection path.

**Acceptance criteria:**

- Every configured member has one backend and one member-keyed readiness source inside its tier runtime.
- Equal member IDs in different tiers resolve to different runtime objects.
- The empty replica-tier URL sentinel never reaches a URL builder.
- Direct tiers still construct exactly one existing backend.

**Verification:**

- `python scripts/run_tests.py tests/router/test_backends.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py tests/router/test_backends.py`

### T008: Route one ordinary response through exactly one member

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/backends/relay.py, tests/router/test_model_routes.py, tests/router/test_backends.py
**Dependencies:** T004, T007

After all request-wide semantic gates and completed readiness snapshots, acquire the atomic compound lease and invoke exactly the selected backend through the existing ordinary-response relay. Never loop around backend invocation. Make backend fakes count calls and cover unavailable, HTTP error, timeout, and pre-response failure paths.

**Acceptance criteria:**

- Four sequential eligible requests select members in the documented stable order.
- A pre-selection unavailable member is skipped without creating a reservation.
- Every success or failure after selection invokes one backend and releases one lease exactly once.
- No failure invokes a peer member, another tier, or another host.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_routes.py tests/router/test_backends.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py anvil_serving/router/backends/relay.py tests/router/test_model_routes.py tests/router/test_backends.py`

### T009: Preserve compound leases across SSE terminal paths

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/backends/sse.py, tests/router/test_streaming_relay.py, tests/router/test_transition_integration.py
**Dependencies:** T008

Carry the selected compound lease through the existing close-aware streaming iterator. Release it exactly once on normal completion, malformed SSE, upstream timeout, cancellation, client disconnect, explicit close, and close-before-first-iteration. Readiness loss after dispatch must not cancel or replace the selected member.

**Acceptance criteria:**

- Every streaming terminal path releases the same tier/member lease exactly once.
- Closing a response before its first iteration releases capacity without invoking a peer.
- Member readiness loss during streaming prevents new selection but does not cancel the in-flight stream.
- Tier drain waits for the stream and completes after its terminal release.

**Verification:**

- `python scripts/run_tests.py tests/router/test_streaming_relay.py tests/router/test_transition_integration.py -x -q`
- `python -m ruff check anvil_serving/router/backends/sse.py tests/router/test_streaming_relay.py tests/router/test_transition_integration.py`

### T010: Extend replica discovery without multiplying aliases

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/discovery.py, tests/router/test_discovery.py
**Dependencies:** T005, T007

Expose one logical tier and bounded safe member state through discovery while retaining one public alias. Do not publish member URLs, topology identity, auth references, or synthetic per-member model aliases.

**Acceptance criteria:**

- Discovery contains one alias and one logical tier for a replica set.
- Member rows are ordered, bounded to the configured maximum, and contain only allowlisted state.
- Direct-tier discovery remains behaviorally identical.

**Verification:**

- `python scripts/run_tests.py tests/router/test_discovery.py -x -q`
- `python -m ruff check anvil_serving/router/discovery.py tests/router/test_discovery.py`

### T011: Record one safe member decision and attempt

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/decision_log.py, tests/router/test_decision_log.py, tests/router/test_observability_hardening.py
**Dependencies:** T008

Record the selected safe member ID, bounded pre-dispatch eligibility/identity reason, and exactly one attempt while preserving the content-free `DecisionLog` schema and ring limits. Serialize explicit allowlists rather than object dictionaries.

**Acceptance criteria:**

- Each admitted replica request records one logical tier, one selected member, and one attempt outcome.
- Pre-selection exhaustion records no selected member and no attempted fallback.
- URL, address, auth, prompt, response, unexpected model, and raw exception substrings are absent.
- Direct-tier decision records remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/decision_log.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py`

### T012: Refuse unsupported replica lifecycle operations before mutation

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serves.py, tests/test_serves_ensure_router.py, tests/router/test_serve_cli.py
**Dependencies:** T006, T007, T009

Validate topology before router-config activation. Make `serves up-for`, promote, mode/profile transition, and rollback paths that assume one backing serve refuse replica tiers with typed `replica_lifecycle_unsupported` before mutation. Operators manage declared members explicitly; do not infer a multi-serve lifecycle.

**Acceptance criteria:**

- Router-config activation refuses an invalid resource/host/endpoint join before writing state.
- Every single-serve lifecycle shortcut refuses replica tiers before lifecycle, route, or profile mutation.
- Direct-tier lifecycle and activation behavior remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_serves_ensure_router.py tests/router/test_serve_cli.py -x -q`
- `python -m ruff check anvil_serving/serves.py tests/test_serves_ensure_router.py tests/router/test_serve_cli.py`

### T013: Document replicas and run integrated release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CONFIGURATION.md, docs/THIN-CAPABILITY-GATEWAY.md, docs/CLI.md
**Dependencies:** T005, T009, T010, T011, T012

Document a generic two-member loopback configuration, exact declared-versus-live identity boundary, round robin, one-attempt behavior, lifecycle refusal, observability, and the later capacity-scheduler boundary. Update generated documentation only through existing generators, then run the complete repository gates without weakening any earlier acceptance criterion.

**Acceptance criteria:**

- Documentation agrees on direct compatibility, same-host topology validation, exact declared provenance, live model verification, no replay, and lifecycle limits.
- No example contains a real address, host identity, credential, or promotion claim.
- Router, full test, lint, strict-doc, link, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `git diff --check`
