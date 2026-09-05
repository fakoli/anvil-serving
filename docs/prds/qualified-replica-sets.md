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
- R004: Before router-config activation, one immutable, at-most-1-MiB exact-byte configuration snapshot must be parsed and every member resource must resolve in validated topology to the declared host and a non-null endpoint normalized-equal to its URL. All member hosts must match. Validation performs no DNS lookup, address-based ownership inference, rendering, or reserialization.
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
- A router config changed on disk after managed snapshot capture cannot change the exact bytes validated and installed by that activation.
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

### A003: Managed snapshot validation is authoritative for same-host ownership.

**Rationale:** The router should not learn real topology or compare network addresses at request time. A bounded pre-activation validator can capture the complete router configuration once, preserve its exact bytes, and use the declared public/private topology boundary while the runtime receives only the validated member set. The snapshot proves declaration consistency, not live deployment identity.

**Requirements:** R003, R004, R012

## Closed v1 implementation contract

- Members contain exactly `id`, `base_url`, `host_id`, `resource_id`, and `qualification_ref`. The capacity follow-on may add `max_concurrency`; unknown keys fail closed. The tier keeps shared auth, health path, timeout, dialect, model, context, output, tool and media policy.
- Member/host/resource IDs use `[A-Za-z][A-Za-z0-9_-]{0,63}`. Qualification references use `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}` and are opaque labels, never paths or URLs. Image/config digests require `sha256:` plus 64 lowercase hex characters; model revision and engine version use `[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}` (1-128 ASCII characters).
- Preserve the direct `Tier.base_url` field; replica tiers use an empty internal sentinel that must never reach a URL builder. Derive per-member direct-tier views with `dataclasses.replace` only after closed-shape validation. Preserve the outer backend map keyed by logical tier; nest members in a tier-owned aggregate.
- Probe/cache/admission keys are composite identities, not concatenated ambiguous strings. Invalidating a logical tier invalidates all its member generations. Stale in-flight probes must not repopulate an invalidated generation; add an event-controlled invalidation race test.
- Round-robin starts at the lexically smallest eligible member ID, rotates only on successful reservation, and skips unavailable members. Request semantic gates happen before one atomic tier/member lease acquisition. No second backend is invoked after selection, including failures before the first response byte.
- Observability reports `deployment_identity_source = "declared"` and `runtime_deployment_identity_verified = false`. Never echo an unexpected upstream model string; expose a fixed mismatch code, with observed model populated only when it equals the validated expected model.
- Managed validation reads at most 1 MiB of router configuration bytes once, parses those exact bytes without a generic serializer, computes their SHA-256, joins resource IDs and normalized endpoint URLs offline, and installs only that validated immutable snapshot. Its fixed error vocabulary is `config_too_large`, `router_config_invalid`, `topology_invalid`, `replica_resource_missing`, `replica_resource_reused`, `replica_host_mismatch`, `replica_endpoint_missing`, `replica_endpoint_mismatch`, and `replica_host_split`; diagnostics expose no path, URL, topology identity, auth metadata, input value, or raw exception. Router lifecycle shortcuts that require one backing serve explicitly refuse replica tiers before mutation; this release does not broaden model lifecycle authority.
- Negative controls must cover identical member names in two tiers, duplicate endpoints, stale probe publication, missing topology resource, wrong resource endpoint/host, unknown identity fields, quiesce/select races, and closing a stream before its first iteration.

## Code Map

- `anvil_serving/router/config.py::Tier`, `RouterConfig`, `_parse_tier`, and `load` own immutable router configuration and the complete alias vocabulary. Add member types and closed-shape validation here; do not infer replicas from URLs or model names.
- `anvil_serving/router/serve.py::build_backends`, `_ConcurrencyLimitedBackend`, and `RoutingBackend` own backend construction and the single resolve/check/relay path. Keep alias resolution outside member selection and preserve the no-fallback dispatch boundary.
- `anvil_serving/router/availability.py::RuntimeModelMetadata`, `AvailabilityResult`, and `HttpHealthAvailability` own bounded health and served-model identity. Introduce member-keyed probes by composition rather than vendor-specific branches.
- `anvil_serving/router/admission.py::AdmissionSnapshot`, `AdmissionLease`, and `TierAdmission` own quiesce/drain semantics. This PRD needs aggregate member reporting but does not yet replace the tier ceiling with capacity scoring.
- `anvil_serving/router/model_metadata.py`, `model_capacity.py`, `discovery.py`, and `decision_log.py::DecisionLog` own metadata-only projections. Add safe member IDs and state without endpoint or topology identity.
- `anvil_serving/router/topology_validation.py` owns the pure topology join and immutable exact-byte validation snapshot. `anvil_serving/router/config.py` owns the bounded bytes parser, with path-based `load` delegating to it; neither module renders or reserializes TOML. `anvil_serving/topology_cli.py` exposes offline validation, while `anvil_serving/router_manage.py` and `anvil_serving/serves.py` own activation and lifecycle refusal. Keep topology validation outside request selection and pass the exact validated bytes into installation.
- `tests/router/test_config.py`, `tests/router/test_availability.py`, `tests/router/test_backends.py`, `tests/router/test_admission.py`, `tests/router/test_model_routes.py`, `tests/router/test_model_metadata.py`, `tests/router/test_decision_log.py`, `tests/router/test_streaming_relay.py`, and `tests/router/test_transition_integration.py` are the primary regression seams.
- `docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`, `docs/adr/0039-capability-meta-router.md`, `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/THIN-CAPABILITY-GATEWAY.md` must agree on the bounded same-host exception.

## Features

### F001: Closed replica configuration contract

Define an explicit same-host member collection, exact shared identity, and immutable pre-activation topology validation while preserving direct tiers.

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

### T005: Deliver safe model and capacity projections

**Feature:** F004
**Priority:** medium
**Type:** modify
**Dependencies:** T005.1, T005.2
**Likely files:** .tickets/2026-09-05-replica-observability-contract.md

Retain this stable task as a coordinator integration-verification task after T005.1 and T005.2. Its claim performs no runtime source implementation: the coordinator confirms both child outcomes, runs the combined gates, and updates the ticket with the original projection commit, wiring commit, and exact combined revision/proof package. T005.1 owns pure allowlisted projections, and T005.2 wires the reviewed projection through the authenticated `RoutingBackend` HTTP path. Explicit dependencies, not implicit dotted-ID parentage, establish the ordering.

**Acceptance criteria:**

- T005.1 and T005.2 are both accepted before the coordinator claim begins.
- The coordinator records both implementation commits and independently reproducible combined proof in the ticket without changing runtime source.
- The combined result satisfies the original projection, authenticated HTTP wiring, provenance, partiality, and redaction contract without changing request dispatch.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/model_metadata.py anvil_serving/router/model_capacity.py anvil_serving/router/serve.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py`

### T005.1: Build pure safe model and capacity projections

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/model_metadata.py, anvil_serving/router/model_capacity.py, tests/router/test_model_metadata.py, tests/router/test_model_capacity.py
**Dependencies:** T003, T004

Project one logical tier with a bounded ordered member list, declared deployment-identity source, runtime-verification flag, aggregate admission, and per-member readiness. Build allowlisted dictionaries explicitly and keep `base_url`, host address, auth environment/value, request fields, unexpected model strings, and exception text out. Read the admission owner once per tier; absent, invalid, or inconsistent state is unavailable, never zero load. Draining implies quiesced, aggregate/member counts are exact integers in `0..2^53-1`, and ineligible member readiness cannot retain a success reason. Do not fetch metrics through the replica-tier empty URL sentinel or infer aggregate KV capacity from per-member estimates. This task is pure projection work and does not modify `router/serve.py` or add HTTP wiring.

**Acceptance criteria:**

- Metadata preserves one public alias and one logical tier.
- Capacity shows aggregate tier admission plus per-member readiness without implying aggregate qualified throughput.
- An explicitly valid admission snapshot with zero active requests for every configured member projects complete zero counts, while an absent, forged, or internally inconsistent owner remains unavailable.
- Declared deployment provenance is clearly separated from live served-model verification.
- Redaction tests prove prohibited data cannot appear in either projection.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/model_metadata.py anvil_serving/router/model_capacity.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py`

### T005.2: Wire capacity projection through authenticated HTTP

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, tests/router/test_model_capacity.py
**Dependencies:** T005.1

Wire `RoutingBackend.model_capacity()` to pass its actual admission owner to the reviewed T005.1 projection. Initialize the default owner with configured replica membership and preserve an explicitly injected owner. Add no request dispatch or member-selection behavior, and never route metrics through the replica-tier empty URL sentinel.

**Acceptance criteria:**

- A real authenticated HTTP capacity request through `RoutingBackend` shows the same active, quiesced, and released member counts as its injected owner.
- Default construction reports complete zero member counts; missing, forged, or inconsistent owner state remains unavailable.
- HTTP output is exactly the reviewed allowlisted T005.1 projection and contains no prohibited field or raw value.
- Ordinary request dispatch and direct-tier capacity behavior remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_capacity.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py tests/router/test_model_capacity.py`

### T006: Build an immutable router-config topology validation snapshot

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/config.py, anvil_serving/router/topology_validation.py, tests/router/test_config.py, tests/router/test_topology_validation.py
**Dependencies:** T002

Add a bytes-based router-config parser and a pure replica topology validator. Bound the captured configuration to 1 MiB before decoding, read it once, preserve its exact bytes, and compute their SHA-256. Make the existing path-based loader delegate to the same parser. Return one frozen validation snapshot containing the bytes with `repr=False`, digest, parsed `RouterConfig`, validated topology object, and exact tier/member counts. Join each member's `resource_id` to validated topology, require an existing resource, exact declared host ownership, a non-null normalized-equal endpoint, one shared host, and distinct resources. Perform no TOML rendering or reserialization, DNS, socket, Docker, service, GPU, or live identity work. Map malformed input and joins to fixed typed errors without path, URL, topology identity, auth metadata, input value, raw exception, or exception chaining.

**Acceptance criteria:**

- A valid direct or replica config is parsed from one at-most-1-MiB byte capture, and the immutable snapshot retains exactly those bytes and their SHA-256.
- Missing resources, missing endpoints, endpoint mismatches, duplicate resources, host-owner mismatches, and cross-host sets fail deterministically with fixed typed errors.
- Path-based loading delegates to the bytes parser, and no generic serializer or second file read is introduced.
- Validation records `deployment_identity_source = "declared"` and `runtime_deployment_identity_verified = false`; it never claims live attestation.
- Direct-tier configuration behavior remains compatible, and negative controls prove validation performs no DNS, network, Docker, service, or GPU call.

**Verification:**

- `python scripts/run_tests.py tests/router/test_config.py tests/router/test_topology_validation.py -x -q`
- `python -m ruff check anvil_serving/router/config.py anvil_serving/router/topology_validation.py tests/router/test_config.py tests/router/test_topology_validation.py`

### T007: Build replica runtime member backends

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, tests/router/test_backends.py
**Dependencies:** T003

Build existing member adapters under a `ReplicaRuntime` aggregate stored at the outer logical tier ID. Derive validated per-member direct-tier views only after configuration loading, preserve the tier-wide dialect, timeout, auth, and semantic policy, and keep the outer backend map keyed by tier ID. Do not create a second alias-resolution or engine-selection path.

The aggregate owns a copied, immutable mapping of exactly the declared member IDs to existing backend adapters. Expose `member_ids`, `member_backend(member_id)`, and `generate_member(member_id, request)`; unknown members fail with a fixed input-free error. Its ordinary `generate(request)` must refuse with a fixed selection-required error and invoke no adapter. T008, not this constructor, selects the member. Build direct views with `replace(tier, base_url=member.base_url, replicas=())` and forward the existing environment, transport, effective timeout and model-discovery arguments unchanged.

Preserve the selected adapter's structured-result side channel. `ReplicaRuntime` keeps a thread-local selected-backend pointer, not a copied result or terminal store. Clear it before lookup/invocation and on refusal or eager failure; retain only the successfully invoked member for that thread. `get_last_structured()` delegates to that member's existing method or returns None. The existing outer wrapper already forwards this method, and `RoutingBackend.complete_metadata()` reads it after normal iterator drain for tool calls, finish reason and usage. T008 must invoke the outer wrapper's `generate_member`, never bypass its ceiling through a raw member backend.

Keep readiness in the single existing `RoutingBackend._availability` provider, whose accepted T003 implementation already keys probes by `(tier.id, member.id)`. Do not add a readiness cache or a provider per replica. T008 calls `safe_check_member` on that owner to complete all snapshots before admission. Likewise, retain exactly one outer `_ConcurrencyLimitedBackend` for the logical tier. Add an explicit `generate_member` forwarding path that acquires its existing semaphore and returns the existing close-aware iterator; do not put a semaphore on each member or multiply the configured tier ceiling. Preserve direct `generate` and structured-result behavior. The source finding and ownership decision are recorded in `.tickets/2026-09-05-replica-runtime-construction.md`.

**Acceptance criteria:**

- Every configured member has one backend in its tier aggregate; readiness remains in the existing single provider under a composite tier/member key, with no new cache or network call during construction.
- Equal member IDs in different tiers resolve to different runtime objects.
- The empty replica-tier URL sentinel never reaches a URL builder.
- Direct tiers still construct exactly one existing backend.
- Aggregate generation without explicit selection and unknown member lookup both refuse before invoking an adapter, without echoing the member operand.
- A tier with a concurrency limit retains one shared outer semaphore. Event-controlled cross-member tests prove it releases on error and on close-before-first-iteration, and cannot admit one request per member beyond the shared ceiling.
- After normal drain, aggregate and outer wrapper expose exactly the selected member's structured tool calls, finish reason and usage. Event-controlled concurrent threads never cross-observe results; refusal and eager failure reset the pointer. Removing the structured-result delegation makes the regression fail.

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
**Dependencies:** T005.2, T007

Expose one logical tier and bounded safe member state through discovery while retaining one public alias. Do not publish member URLs, topology identity, auth references, or synthetic per-member model aliases.

Reuse the accepted `model_capacity.replica_metadata(tier, availability)` projection
once per logical tier per payload. For each existing configured alias entry backed
by that replica tier, add exactly `logical_tier` (the configured tier ID), `members`,
`replica_identity`, `deployment_identity_source` and
`runtime_deployment_identity_verified`; the last four values come only from the
shared allowlisted projection. Do not synthesize an extra model entry for the tier
or any member, remove unavailable aliases, multiply context/concurrency limits,
or adopt member runtime metadata. Multiple deliberately configured aliases remain
multiple entries as before, sharing one cached projection for the request. Skip
the direct `safe_check`/runtime-tier resolution branch for replicas, including
when no availability provider exists; the empty replica base URL never reaches a
direct probe. Keep iterable-only and direct-tier output byte-for-behavior unchanged.
Test through the actual front-door discovery endpoint as well as the pure helper.

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
**Likely files:** anvil_serving/router/decision_log.py, anvil_serving/router/serve.py, tests/router/test_decision_log.py, tests/router/test_observability_hardening.py
**Dependencies:** T008

Record the selected safe member ID, bounded pre-dispatch eligibility/identity reason, and exactly one attempt while preserving the content-free `DecisionLog` schema and ring limits. Serialize explicit allowlists rather than object dictionaries.

Add only two optional DecisionRecord fields, `replica_member_id` and
`replica_selection`, both default None. A selected member uses its configured
ID and selection `identity_passed`; a refused compound admission uses no member
and `not_admitted` (which deliberately does not distinguish readiness, drain or
capacity); a replica request refused by an earlier semantic/backend-binding
gate uses no member and `request_rejected`. Direct records keep both absent.
Do not add a per-member readiness map, raw identity, second attempt field or
another probe. Carry the selected lease member through every eager-error,
completion, completion-error, stream-error and cancellation call to `_record`.
For replica records before member selection, attempts is empty; after selection
it contains exactly the existing single logical-tier AttemptRecord, including
close-before-first-iteration. Selection is not successful generation.

For replica attempts normalize reasons to the fixed set served,
served_output_clamped, client_disconnected, backend_error, completion_error;
never retain exception class names or provider strings. Direct reason behavior
is unchanged. Validate new metadata before memory, summary, audit line and JSONL
output: exact built-in strings, configured member grammar
`[A-Za-z][A-Za-z0-9_-]{0,63}`, and the closed selection enum/pair rules above.
Invalid optional metadata is dropped as a pair without echoing values; no
arbitrary string conversion. Routing must only stamp a member actually declared
in the selected tier. Summary also accepts untrusted mappings, so it must reuse
the same bounded projection rather than pass through arbitrary nested values.
Append the two new audit labels only when valid/present; omit None fields from
summary and JSONL to preserve legacy direct output. Replace JSONL's broad
dataclasses.asdict projection with an explicit list of the existing record and
attempt fields plus these two, preserving every prior direct/workload field and
its omission behavior, timestamp stamping, ring limit and rotation behavior.
The exact wiring gap and acceptance scope are tracked in
`.tickets/2026-09-05-replica-decision-wiring.md`.

**Acceptance criteria:**

- Each admitted replica request records one logical tier, one selected member, and one attempt outcome.
- Pre-selection exhaustion records no selected member and no attempted fallback.
- URL, address, auth, prompt, response, unexpected model, and raw exception substrings are absent.
- Direct-tier decision records remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/decision_log.py anvil_serving/router/serve.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py`

### T012: Refuse unsupported replica lifecycle operations before mutation

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serves.py, anvil_serving/commands/serves.py, tests/test_replica_lifecycle.py
**Dependencies:** T006, T007, T009, T012.1, T012.2, T012.3

Make `serves up-for`, promote, resume, mode/profile enter or leave, rollback, and recovery paths that assume one backing serve refuse replica tiers with typed `replica_lifecycle_unsupported` before lifecycle, route, profile, Docker, or router-transition mutation. Apply the shared guard at each entry point rather than relying on a later config-install refusal. Operators manage declared members explicitly; do not infer a multi-serve lifecycle. Router-config snapshot activation is separate T015.

T012 is the integration gate for the three children below. Their shared contract
closes the missing active-config ownership described in
`.tickets/2026-09-05-replica-lifecycle-config-ownership.md`. A manifest's
router_tier string is not proof of direct membership. Add
ReplicaLifecycleUnsupported(ValueError) with fixed code and message
replica_lifecycle_unsupported, plus a pure guard on parsed RouterConfig and the
affected tier IDs. Missing/unloadable config or an absent affected tier uses
the separate fixed refusal replica_lifecycle_configuration_unavailable; never
silently treat unavailable metadata as direct or echo paths/parser details.
Existing command wrappers retain their nonzero exit convention and render the
fixed code. No live router query or invented topology is permitted.

Promotion checks exactly affected_tiers in both promoted and rollback configs,
before the global lock and at every _promotion_transition entry. Up-for checks
the resolved alias's tier before candidate selection. Mode/profile checks a
static union of the target's tier, all potential GPU victims' nonempty tiers,
and all restore-group members' nonempty tiers, including currently stopped
members. Validate that union in the active config; if the exclusive target owns
promoted/rollback profiles, also validate its target tier in both. Check before
state probes, profile noop, locks, journals, events or transitions. mode status
and profile list remain unchanged. A transition with no routed affected tiers
does not require a config.

Give cmd_mode and cmd_profile an optional parsed active_config keyword. For
routed transitions, the CLI loads --config or the existing operator-home-only
doctor.resolve_default_config_path; direct library callers supply a parsed
config. Do not infer an active config from a target/rollback profile or the
current working directory. Missing config for a routed transition is an
intentional fail-closed compatibility change; previously proven direct behavior
remains unchanged when the explicit/default direct config exists. Add --config
to local mode preview/enter/leave and profile preview/apply declarations and
parsers only. Existing remote serves_mode uses the remote owner's default
config; explicit --config is locally supported and refused by remote dispatch,
not dropped or forwarded as an arbitrary remote filesystem path. Do not change
MCP schema or privileges in this slice.

**Acceptance criteria:**

- Every single-serve lifecycle shortcut, including rollback and recovery branches, refuses replica tiers before lifecycle, route, profile, Docker, or router-transition mutation.
- Event-controlled spies prove invalid operations make zero mutation calls; guarding only the eventual router-config install is insufficient.
- Direct-tier lifecycle and activation behavior remain unchanged with a proven direct config; routed transitions without one fail before mutation.

**Verification:**

- `python scripts/run_tests.py tests/test_replica_lifecycle.py tests/test_serves.py tests/test_serves_up_for.py tests/test_serves_manage.py tests/test_serves_profiles.py tests/test_serves_preflight_gate.py tests/test_events.py tests/test_recipe_container_discovery.py tests/test_serves_ensure_router.py tests/router/test_serve_cli.py -x -q`
- `python -m ruff check anvil_serving/serves.py anvil_serving/commands/serves.py tests/test_replica_lifecycle.py tests/test_serves_manage.py tests/test_serves_profiles.py tests/test_serves_preflight_gate.py tests/test_events.py tests/test_recipe_container_discovery.py`

### T012.1: Guard promotion and alias shortcuts before mutation

**Feature:** F001
**Priority:** high
**Type:** modify
**Dependencies:** T006, T007, T009
**Likely files:** anvil_serving/serves.py, tests/test_replica_lifecycle.py

Implement the shared typed guard from parent T012. Apply it to up-for before
candidate selection, promotion topology validation, cmd_promote before its lock,
and every _promotion_transition entry, covering resume, explicit/automatic
rollback and promoted-state recovery. resolve_recipe_activation already runs
promotion validation before switch journals/locks; test that real path. Use
both complete configs and only the exact affected tiers. An unrelated replica
tier must not reject a direct-only promotion. Missing affected membership is a
fixed configuration refusal. Do not implement mode/profile yet.

**Acceptance criteria:**

- Valid replica config refuses with the fixed code before lock/journal/transition/install/Docker/lifecycle-event calls in every promotion direction and recovery entry.
- Up-for refuses replicas even with zero, one or multiple apparent serve backers; unknown aliases keep existing behavior.
- Direct controls and unrelated-replica controls preserve existing behavior.
- Bypassing the guard makes a zero-mutation regression fail; restore it.

**Verification:**

- `python scripts/run_tests.py tests/test_replica_lifecycle.py tests/test_serves.py tests/test_serves_up_for.py -x -q`
- `python -m ruff check anvil_serving/serves.py tests/test_replica_lifecycle.py`

### T012.2: Own active router configuration for mode and profile transitions

**Feature:** F001
**Priority:** high
**Type:** modify
**Dependencies:** T012.1
**Likely files:** anvil_serving/serves.py, anvil_serving/commands/serves.py, tests/test_replica_lifecycle.py

Implement parent T012's active_config argument, static affected-tier union,
early mode/profile/noop guard and local --config wiring. Use the existing
operator-home default resolver only at CLI ownership; pass parsed config to
library calls and through profile-to-mode delegation. Keep status/list and
entirely unrouted transitions unchanged. Missing/unloadable config and missing
affected tiers are fixed configuration refusals, not direct membership. Test
actual local CLI/default loading and remote explicit-config refusal without a
transport request. Do not loosen MCP schema or create an active route registry.
T012.3 owns explicit direct-fixture migration; T013 owns manifest regeneration
and final docs, so do not suppress either later gate.

**Acceptance criteria:**

- An unrouted exclusive target with a replica-backed victim/restore member is refused before state probes or mutation, including stopped potential victims.
- Replica target, active profile, rollback profile and apparent profile noop cannot bypass the guard.
- Missing, malformed and incomplete config fails closed; no routed tiers needs no config; status/list remain read-only and unchanged.
- Local explicit/default config reaches the same parsed guard; remote --config is refused without transport invocation.
- Direct controls supplied with parsed config retain transaction order and rollback semantics; removing early preflight makes the bypass regression fail.

**Verification:**

- `python scripts/run_tests.py tests/test_replica_lifecycle.py -x -q`
- `python -m ruff check anvil_serving/serves.py anvil_serving/commands/serves.py tests/test_replica_lifecycle.py`

### T012.3: Make existing direct lifecycle fixtures explicit

**Feature:** F001
**Priority:** high
**Type:** modify
**Dependencies:** T012.2
**Likely files:** tests/test_serves_manage.py, tests/test_serves_profiles.py, tests/test_serves_preflight_gate.py, tests/test_events.py, tests/test_recipe_container_discovery.py

Update only existing routed mode/profile fixtures and call sites to provide
the newly required synthetic parsed active config or temporary operator-home
config. Preserve all prior transaction, failure, rollback, readiness, event and
ownership assertions; do not monkeypatch out the guard or turn routed fixtures
into unrouted ones. Extend fake delegated signatures only for active_config.
Entirely unrouted and read-only tests remain unchanged. Report any additional
fixture owner outside this list instead of silently editing it.

**Acceptance criteria:**

- Existing direct-mode/profile tests run with explicit synthetic membership and preserve their original behavioral assertions.
- All five legacy test files pass without bypassing the new guard or consulting real operator configuration.
- No runtime, new privileges, live service or private state changes occur.

**Verification:**

- `python scripts/run_tests.py tests/test_serves_manage.py tests/test_serves_profiles.py tests/test_serves_preflight_gate.py tests/test_events.py tests/test_recipe_container_discovery.py -x -q`
- `python -m ruff check tests/test_serves_manage.py tests/test_serves_profiles.py tests/test_serves_preflight_gate.py tests/test_events.py tests/test_recipe_container_discovery.py`

### T013.1: Synchronize the generated command manifest

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI-COMMAND-MANIFEST.json
**Dependencies:** T012.2, T014, T015

Regenerate the existing manifest with anvil_serving.commands.spec.write_manifest
after the implemented CLI declarations. Do not edit JSON by hand, change CLI
behavior, add arguments or weaken parity tests. The integration run at source
7473b866 found only the existing deterministic manifest parity test failing:
five local mode/profile --config options and the updated install-config help
summary were absent from the checked-in artifact. This leaf unblocks combined
regression runs before the remaining narrative documentation task.

**Acceptance criteria:**

- The artifact is byte-identical to deterministic regeneration and includes exactly the implemented command declarations.
- The delta contains only the five implemented local mode/profile --config options and existing install-config summary correction at this candidate baseline; unexpected changes are investigated before committing.
- Existing command-tree tests pass without changes to tests, parser behavior or command authority.

**Verification:**

- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `python scripts/run_tests.py tests/test_command_tree.py -x -q`
- `git diff --check`

### T013: Document replicas and run integrated release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CONFIGURATION.md, docs/THIN-CAPABILITY-GATEWAY.md, docs/cli/control-plane.md, docs/cli/serves.md
**Dependencies:** T005.2, T009, T010, T011, T012, T013.1, T014, T015

Document a generic two-member loopback configuration, exact declared-versus-live identity boundary, immutable snapshot validation, `anvil-serving topology validate-router-config`, round robin, one-attempt behavior, lifecycle refusal, observability, and the later capacity-scheduler boundary. T013.1 already owns manifest regeneration; the repeated write_manifest gate here must leave its current artifact unchanged. Run the complete repository gates without weakening any earlier acceptance criterion. This remaining task edits only the four narrative documentation files, not the generated manifest or product behavior.

Document mode/profile's local --config option, operator-home default and parsed
library requirement for routed transitions, the fixed missing-config refusal,
remote explicit-config refusal, and unchanged status/list behavior.

**Acceptance criteria:**

- Documentation agrees on direct compatibility, same-host topology validation, exact declared provenance, live model verification, no replay, and lifecycle limits.
- CLI documentation and the generated manifest agree on the root `topology validate-router-config` path, options, read-only authority, result schema, and exit contract.
- No example contains a real address, host identity, credential, or promotion claim.
- Router, full test, lint, strict-doc, link, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `git diff --check`

### T014: Expose offline router-config topology validation

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/topology_cli.py, anvil_serving/commands/control_plane.py, anvil_serving/cli.py, tests/test_topology.py
**Dependencies:** T006

Add the root command `anvil-serving topology validate-router-config --config PATH [--topology PATH] [--topology-overlay PATH] [--json]` to the existing local offline topology family. It must call the T006 snapshot validator without target resolution, transport, confirmation, network, Docker, service, GPU, or mutation behavior. Library code returns a dictionary; only wrappers print. The result has exactly `schema_version`, `valid`, `error_code`, `config_sha256`, `tier_count`, `replica_tier_count`, `replica_member_count`, `deployment_identity_source`, and `runtime_deployment_identity_verified`, with schema literal `replica-topology-validation/v1`, success provenance `declared`, and false runtime verification. Failure uses the same keys, a T006 fixed error code, null counts and provenance, false runtime verification, and a null digest unless a complete bounded capture is available without rereading. The existing root CLI JSON envelope retains this dictionary in `data` for both success and refusal. Keep its command label exactly `topology validate-router-config`, context null, warnings empty, and errors fixed/input-free; never echo argv or paths in any wrapper field. Human output is one bounded line derived only from that dictionary. Exit zero means valid and exit two means refused.

The root dispatcher special-cases the topology family before handler resolution. Extend that actual branch in `cli.py::_dispatch`, not only `topology_cli.main`; preserve other topology commands. Its generic `_json_envelope` normally includes raw argv for topology commands, so the new sensitive leaf also needs operand-free envelope handling. Invalid/missing/repeated flags must refuse without argparse echoing raw inputs or invoking the validator. Follow the existing protected metadata command idiom without broadening other commands' output contracts.

**Acceptance criteria:**

- The exact root CLI path and four options work locally and return the closed success or failure dictionary.
- JSON data and human output derive from the same value; the entire root envelope, parser errors and human line contain no config path, URL, host or resource identity, auth metadata, raw value, or exception text. Real `cli.main` tests exercise valid, invalid, missing/repeated/unknown flag, and private-path cases.
- Invalid config/topology/join input exits two; a valid direct or replica snapshot exits zero without modifying a file or contacting a host.
- A negative control proves the command invokes the shared T006 validator and cannot pass through an independently shaped fixture.

**Verification:**

- `python scripts/run_tests.py tests/test_topology.py -x -q`
- `python -m ruff check anvil_serving/topology_cli.py anvil_serving/commands/control_plane.py anvil_serving/cli.py tests/test_topology.py`

### T015: Activate only the validated exact-byte router snapshot

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router_manage.py, anvil_serving/serves.py, anvil_serving/commands/router.py, tests/test_router_manage.py
**Dependencies:** T006, T012

Make managed `router install-config` capture and validate one T006 snapshot before its first status transition or other mutation, then pass the snapshot's exact bytes to the installer. The installer must not reopen the source path, normalize newlines, or serialize the parsed configuration. A path-based internal install remains compatible for direct configurations but rejects any replica-bearing config with `replica_lifecycle_unsupported`; only the managed activation path may install replicas, and only through the validated snapshot. Forward the existing topology and optional overlay selection to local activation without adding DNS, service discovery, raw Docker validation, or live identity claims. A prior standalone T014 success is informational, never an activation token; activation always recaptures and revalidates its own snapshot.

**Acceptance criteria:**

- Invalid resource, host, endpoint, duplicate-resource, or cross-host joins fail before `_transition("status")`, quiesce, drain, file write, restart, or any other mutation.
- The bytes validated are exactly the bytes supplied to the deployed validator and atomic writer; changing the source path after capture cannot change installed bytes.
- The installer never reopens or newline-normalizes a validated snapshot, and its fixed failures contain no path, URL, topology identity, auth metadata, input value, or raw exception.
- Internal path-based lifecycle installs refuse replicas, while direct-tier install behavior remains compatible.

**Verification:**

- `python scripts/run_tests.py tests/test_router_manage.py tests/test_serves_ensure_router.py -x -q`
- `python -m ruff check anvil_serving/router_manage.py anvil_serving/serves.py anvil_serving/commands/router.py tests/test_router_manage.py`
