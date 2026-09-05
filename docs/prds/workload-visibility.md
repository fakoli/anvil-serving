# Project: Unified Workload Visibility

## Summary

Add one versioned, metadata-only workload projection that lets operators answer what Anvil Serving is doing now and what recently finished across router requests, controller operations, benchmark jobs, media jobs, and managed recipe serves. Each owning subsystem remains authoritative and keeps its existing lifecycle; the unified surface reads bounded projections locally or through the authenticated controller, reports partial fleet results honestly, and never becomes a scheduler or mutation API.

## Goals

- Define one bounded `WorkloadRecord` schema shared by router, controller, CLI, MCP, dashboard, and fleet aggregation surfaces.
- Show active and recent work from existing authoritative stores without replacing their state machines.
- Track router request lifecycle from validated alias through terminal outcome without retaining request or response content.
- Aggregate declared fleet nodes through exact expected-node controller transport and preserve per-node unavailable states.
- Give a focused execution model explicit projection seams, redaction rules, task order, fixtures, and independent verification commands.

## Non-Goals

- Starting, stopping, cancelling, pausing, reprioritizing, rescheduling, retrying, or promoting any workload.
- Using workload data as router selection, admission, lifecycle, placement, promotion, or automatic recovery input.
- Distributed tracing, log aggregation, prompt inspection, response inspection, billing, or a general metrics database.
- Cross-host scheduling, discovery of undeclared nodes, SSH polling, or waking sleeping hosts.
- Replacing `DecisionLog`, `OperationStore`, `BenchmarkJobStore`, `MediaJobStore`, recipe lifecycle state, or type-specific mutation commands.
- Exposing endpoint URLs, network addresses, user paths, environment values, credentials, prompts, messages, tool arguments/results, media inputs, commands, raw exceptions, or response bodies.
- Claiming that configuration, a database row, health 200, or historical completion proves a workload is currently running.

## Requirements

- R001: Define immutable versioned `WorkloadRecord` and query/result types according to the closed v1 contract below. No free-text labels, phases, progress messages, outcome messages, or caller-supplied correlation values may enter the projection.
- R002: Support exactly `router-request`, `controller-operation`, `benchmark-job`, `media-job`, and `recipe-serve`. Unknown owner states map to `unsupported` without retaining raw text; unknown incoming kinds/schema are rejected as a typed source failure.
- R003: Each source adapter must read the authoritative subsystem state and project it into `WorkloadRecord`; the unified registry must not become a second source of truth or write state back to the owner.
- R004: The router may generate an internal gateway request ID earlier, but it must create active metadata only after front-door authentication, bounded request parsing, and successful configured-alias resolution. It then advances that record through the ordered active states `checking`, `admitted`, `dispatched`, and `streaming` at the existing admission/backend/iterator boundaries; caller-derived correlation values never identify workloads.
- R005: Router completion is two phase: backend exhaustion or failure proposes a safe terminal result, while the front door commits it only after buffered or streaming response delivery and final flush. Every success, error, timeout, cancellation, client disconnect, socket-write failure, stream close, and close-before-first-iteration path must remove the active record and append at most one bounded terminal record to the existing content-free `DecisionLog`; workload observation failure must never alter routing or admission.
- R006: Owner stores expose deterministic bounded list/recent projections under their existing locks/transactions. v1 uses limits and truncation, not pagination/cursors; workload code must never query another module's SQLite tables directly.
- R007: Managed recipe serves must be projected from Anvil-owned recipe/container state and stable labels, distinguish recipe-owned from manifest-owned serves, and report configured, observed, stale, absent, or error provenance without equating container existence with a healthy serving identity.
- R008: Remote collection uses authenticated `ControllerTransport(expected_node=...)`. Validate schema, envelope node, every record host, bounds, and timestamps; wrong identity/schema, malformed records, future-skew violation, timeout, or unreachable controllers produce per-node errors while preserving peers.
- R009: A declared sleeping or unreachable host must remain visible as an unavailable node summary, and the fleet result must report `complete`, `partial`, or `unavailable` with collection timestamps and freshness instead of presenting missing hosts as idle.
- R010: Explicit allowlists apply to the entire response/context/error/log/rendered envelope, not just records. Exclude payloads, paths, URLs, addresses, credentials, environment, commands, SQL, and exceptions. Never serialize generic ExecutionPlan/TransportError dictionaries.
- R011: Unified workload surfaces are operator-only and require per-client `workloads:read`; legacy/media-only credentials receive no unified tool. CLI, authenticated router/controller endpoints, MCP and dashboard share canonical records and query semantics. Node controllers collect router state from its declared authenticated local source endpoint; fleet and dashboard query controllers.
- R012: Queries must support bounded filters for kind, state, safe host ID, owner, and active/recent window; results must use stable newest-updated ordering with record-ID tie breaks, include truncation metadata, and apply hard per-source and aggregate limits.
- R013: Type-specific mutation commands remain the only mutation authority; workload records may include safe links or identifiers for those commands, but the unified API and dashboard must be read-only.
- R014: Workload data must never feed router choice, admission, serve lifecycle, controller execution, promotion, or automated remediation; implementation must remain stdlib-only, return structured values from library code, and use injected clocks for deterministic tests.

## Acceptance Criteria

- Each fixture record has byte-identical canonical serialization through CLI, endpoint, MCP/controller, fleet, and dashboard. Envelopes may add collection timestamps, node status, and truncation metadata; those wrappers are not claimed byte-identical.
- Router tests cover ordinary success, rejection before admission, eager backend failure, upstream HTTP error, timeout, cancellation, disconnect, normal SSE completion, malformed SSE, close-before-first-iteration, buffered and streaming socket-write failure, and final-flush failure. Active records always return to zero and exactly one safe terminal projection remains where policy allows; the actual `build_server` path uses the same registry and `DecisionLog` as `RoutingBackend`.
- Controller operation, benchmark, and media stores return deterministic bounded snapshots without direct cross-module SQL access; concurrent writer/reader tests never expose partial rows.
- Recipe projections distinguish configured, observed-running, healthy-identity, stale, absent, and inspection-error states without claiming deployment or qualification.
- A fleet query with one healthy node, one wrong-identity node, and one unreachable/sleeping node returns the healthy records plus explicit error rows and overall `partial` status.
- Redaction/adversarial tests seed every prohibited field and prove none appear in JSON, text, logs, endpoint errors, or dashboard bootstrap data.
- Every query surface enforces filters, stable ordering, freshness, per-source/aggregate limits, and truncation metadata.
- Focused tests, full tests, lint, strict docs, link checks, command-manifest checks, and diff checks pass.

## Risks

- A unified registry can accidentally become duplicate mutable state and drift from owner stores; projections must remain read-only and derived.
- Router lifecycle cleanup is easy to miss on disconnect and SSE generator close paths, causing ghost active requests or memory growth.
- Committing backend success before the front door delivers and flushes the response can falsely report success after a socket failure; terminal proposal and delivery-aware commit must remain separate and idempotent.
- Seemingly harmless labels or exception text can leak prompts, endpoints, filenames, or private topology; serialization must be allowlisted at the schema boundary.
- Fleet fan-out can turn one unavailable node into a slow global query; per-node deadlines and bounded parallel collection are required.
- Recipe/container inspection can confuse historical configuration, physical occupancy, health, and live identity; provenance fields must keep them separate.
- Dashboard-specific transformations can fork schema semantics; all UI data must come from the same canonical serializer.

## Open Questions

None. The first release is read-only, bounded, metadata-only, controller-collected, partial-result preserving, and explicitly excluded from routing or lifecycle decisions.

## Assumptions

### A001: Owning stores and runtime registries remain authoritative.

**Rationale:** Existing components already define valid transitions and persistence. A projection layer can unify visibility without creating cross-component write coupling or a second state machine.

**Requirements:** R003, R006, R007, R013, R014

### A002: Recent history is bounded per source rather than retained indefinitely.

**Rationale:** The operator question is current/recent activity, while durable benchmark evidence and operation audit data already have type-specific stores. Hard limits bound memory, latency, and disclosure risk.

**Requirements:** R001, R005, R006, R012

### A003: Fleet completeness is itself observable data.

**Rationale:** A sleeping, unreachable, wrong-identity, or incompatible node cannot be safely interpreted as idle. Explicit partial/unavailable results preserve the distinction between no work and no evidence.

**Requirements:** R008, R009, R012

## Closed v1 implementation contract

- Schema is `anvil-workloads/v1`. Records use UTC source timestamps plus a separate collection timestamp. ID is a deterministic opaque digest of node, kind, owner and owner-generated source ID; router request IDs are generated internally. Do not hash prompts, credentials or payloads to manufacture IDs.
- Owner enum is `router|controller|benchmark|media|recipe|manifest`. State and phase mappings are fixed by the table below. Outcomes are `success|error|cancelled|timeout|rejected|disconnected|unavailable|unknown` or absent for active work. Unknown owner states map to `unsupported` without retaining raw text.
- Display labels come only from kind or a known command-catalog identifier, never job titles, filenames, exception text or caller strings. Progress is optional `{completed,total,unit}`, with nonnegative integers up to 1000000000, completed <= total, and unit `items|steps|requests`; no prose progress.
- Per-source result limit is 200; aggregate limit is 1000; recent window is 1-86400 seconds (default 3600). Default query is active plus recent terminal work. Validate kind/state/host/owner filters against the same schema, reject unknown/repeated scalar filters, and sort newest source-updated first with record ID ascending as tie break.
- `anvil_serving/router/workloads.py` is the sole router workload owner. `RouterWorkloadRegistry(decision_log, *, clock, max_active=1024)` returns an initially inert `RouterWorkloadToken` from `begin(gateway_request_id)`; `activate()` creates `checking` only after a configured alias resolves, and `advance()` accepts only the ordered active states `checking|admitted|dispatched|streaming` idempotently.
- `RoutingBackend.generate_tracked(request, *, gateway_request_id)` is the authenticated runtime seam. Existing `generate()` remains behavior-compatible and untracked for direct callers. `build_server` creates one registry over the exact `DecisionLog` used by its `RoutingBackend` and exposes that registry to the authenticated workload endpoint; it must not construct an unused parallel registry.
- A token's `propose_terminal(decision, outcome)` retains one safe pending proposal without appending a second ring. `finish(delivery_outcome=None)` commits at most one existing `DecisionLog` record under the registry's finalization guard and removes active state in `finally`. A delivery disconnect may override workload outcome without rewriting legacy decision fields. Normal success is committed only after response bytes, stream terminator, and any buffered flush complete; fallback close commits the pending backend failure or `disconnected`.
- `RouterWorkloadStream` owns delivery finalization for the wrapped admission iterator and exposes `finish_delivery(outcome)`. No registry lock may span readiness, admission, backend/network work, response delivery, or a slow decision sink. Clock, registry, saturation, projection, and cleanup failures are observational only and must not fail or change routing/admission.
- Router active registry cap is 1024. Saturation tracks the exact number of currently unrepresented active requests in truncation/omission metadata but never affects request success; a saturated token may still yield terminal `DecisionLog` projection. Recent terminal projection uses `DecisionLog.recent(limit=512)` and never a second terminal store. Terminal entries supersede same-ID active entries.
- `DecisionRecord` may add only exact UTC microsecond-Z workload timestamps and the fixed workload outcome. The internally generated `gateway_request_id` is the owner-native identity; caller request/workbench/task IDs and payload-derived values are never workload identity or projected fields. `source_result(host, query, now)` receives the trusted configured host from the later endpoint/collection seam, derives the canonical digest there, and never infers host identity from bind address, machine hostname, or caller input.
- Provenance has separate `source_authority` (`router-memory|controller-store|benchmark-store|media-store|managed-status`) and `observation_quality` (`recorded|configured|observed-running|healthy-identity|stale|absent|inspection-error`). Health-only never maps to healthy-identity. Both recipe/manifest serves use kind `recipe-serve` with their distinct owner.
- Each node response identifies its expected node and every record must match it. Preserve source time and collection time; source times more than 30 seconds in the future fail that source. Freshness uses source observation age (30-second default stale threshold), never collection time relabeled as observation.
- Node controller reads its own stores through bounded public projection methods and router active/recent state through authenticated `GET /v1/workloads` at a topology-declared loopback router resource. Missing source/config/auth is explicit unavailable, not idle. It must not open the router database or discover ports.
- Fleet queries only expected-node controller workload operations, with at most four concurrent calls, 2-second per-node and 5-second collection deadlines, no unbounded queued work, and a visible result for every declared node. Failed or sleeping nodes remain visible. Dashboard consumes this same node/fleet result; it never reconstructs records from DOM or hardware samples.
- All unified reads require a per-client `workloads:read` operator grant. Router data-plane bearer tokens and media-only principals are not implicitly workload operators. The shared authorization prerequisite is fleet-node-enrollment:T008; denial occurs before collection, including router/controller/MCP/dashboard.
- Canonical record bytes are shared across surfaces. Envelopes add node/source status, collection time, completeness, and truncation; never pass generic command/transport context through them. Whole-envelope adversarial tests seed credentials, user paths, tool payloads, URLs, private addresses and raw exceptions.
- Every node envelope includes per-source `complete|partial|unavailable` status. A malformed/future router source becomes unavailable without discarding healthy store/serve sources; any surviving source makes node status partial, and only all-source failure makes it unavailable. Fleet retains that node status.

### Fixed owner mappings

The table is the complete phase vocabulary. State vocabulary is the union of
the state column; unknown inputs use the final row. Store projections have
observation quality `recorded`; managed status chooses the explicit quality
below. Router states describe actual boundaries, never inferred response text.

| Owner/input | State | Phase | Outcome |
| --- | --- | --- | --- |
| Router checking/admitted/dispatched/streaming | Same named state | Same named phase | absent |
| Router terminal event | terminal | completed/failed/cancelled as appropriate | success/error/cancelled/timeout/rejected/disconnected |
| Controller running | running | running | absent |
| Controller succeeded/failed | terminal | completed/failed | success/error |
| Benchmark queued/running | queued/running | queued/running | absent |
| Benchmark completed/failed/cancelled | terminal | completed/failed/cancelled | success/error/cancelled |
| Media accepted/queued | queued | queued | absent |
| Media awaiting_approval | queued | awaiting-approval | absent |
| Media preparing/submitting | running | preparing/submitting | absent |
| Media running | running | running | absent |
| Media completed/failed/canceled | terminal | completed/failed/cancelled | success/error/cancelled |
| Managed configured-only | configured | configured | absent |
| Managed observed-running or health-plus-identity | running | running | absent |
| Managed absent | absent | absent | absent |
| Managed inspection error | unavailable | unavailable | unavailable |
| Any unknown owner state | unsupported | unsupported | unknown |

Managed stale observations retain their last supported state with quality
`stale`; they never become proof of current running. An active-only filter
includes checking/admitted/dispatched/streaming/queued/running records that
are not stale; default active-plus-recent output additionally includes current
configured/absent/unavailable/unsupported observations and terminal entries
within the recent window.

## Code Map

- `anvil_serving/observability/schema.py::TelemetrySample` and `anvil_serving/observability/api.py::TelemetryRegistry` provide immutable schema, bounded registry, injected-clock, and API serialization patterns. Workloads should be a sibling projection module, not fields bolted onto hardware telemetry.
- `anvil_serving/router/workloads.py::RouterWorkloadRegistry` owns active router projection, phase ordering, terminal proposal, delivery-aware finalization, saturation, and active/recent deduplication. `decision_log.py::DecisionLog` remains the only terminal store and supplies a bounded `recent(limit)` copy.
- `anvil_serving/router/front_door.py` owns authentication, bounded request parsing, response delivery and final flush; `serve.py::RoutingBackend` owns configured-alias resolution, readiness, admission and backend dispatch; `backends/relay.py` supplies only a fixed safe timeout/error discriminator at its injected transport boundary. Existing stream framing/cleanup remains in `backends/sse.py` and the front door; it is not a second workload owner.
- `anvil_serving/router/router_telemetry.py` shows how bounded aggregates derive from `DecisionLog`; do not add prompts or raw records to make workload visibility easier.
- `anvil_serving/control_plane/controller/store.py::BenchmarkJobStore` and `OperationStore` own durable controller job state. Add public bounded projection methods next to existing locked/transactional reads.
- `anvil_serving/media/jobs.py::MediaJobStore` owns media-job lifecycle and cancellation; workload visibility must call a safe list projection rather than access storage internals.
- `anvil_serving/serve_recipes.py` and `serves.py` distinguish recipe-managed and manifest-managed state, container observation, health, model identity, and lifecycle ownership.
- `anvil_serving/transports.py::ControllerTransport(expected_node=...)` provides pre-dispatch identity verification. `anvil_serving/observability/probes/remote_controller.py::collect_remote_telemetry` provides post-response host/schema validation but does not currently pass `expected_node`; workload collection must explicitly pass it and retain record-host validation.
- `anvil_serving/observability/dashboard/app.py` and its static/template modules own the existing local dashboard; add a workload panel without a parallel server or client-only schema.
- `anvil_serving/commands/spec.py::write_manifest`, CLI modules, and controller tool registration own user-visible command/MCP surfaces and generated documentation.
- Primary tests are `tests/router/test_front_door.py`, `tests/router/test_streaming_relay.py`, `tests/router/test_decision_log.py`, `tests/router/test_observability_hardening.py`, `tests/test_controller.py`, `tests/test_benchmark_jobs.py`, `tests/media/test_jobs.py`, `tests/test_serve_recipes.py`, `tests/observability/test_remote_controller.py`, `tests/observability/test_api.py`, `tests/observability/test_dashboard.py`, and `tests/observability/test_status_redaction.py`.

## Features

### F001: Canonical workload projection schema

Define bounded records, source adapters, filtering, ordering, freshness, limits, and safe serialization without duplicating owner state.

**Requirements:** R001, R002, R003, R010, R012, R013, R014

### F002: Router active and recent workload lifecycle

Track authenticated valid-alias requests through admission, dispatch, iteration, response delivery, and final flush, then project safe terminal metadata from the existing `DecisionLog`.

**Requirements:** R004, R005, R010, R014

### F003: Controller, media, recipe, and fleet aggregation

Read bounded owner snapshots, distinguish observation provenance, and collect remote nodes with exact identity and partial results.

**Requirements:** R003, R006, R007, R008, R009, R012

### F004: Consistent read-only operator surfaces

Expose the same schema through CLI, authenticated APIs, MCP/controller tools, and dashboard with no new mutation authority.

**Requirements:** R010, R011, R012, R013, R014

## Tasks

### T001: Define the workload schema, query, and source adapter contracts

**Feature:** F001
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/workloads.py, tests/observability/test_workloads.py

Create immutable enums/value objects for workload kind, state, provenance, freshness, records, source results, node results, aggregate status, filters, and truncation metadata. Implement one canonical allowlisted serializer and stable ordering/filter/limit helpers. Executor guidance: follow `TelemetrySample` validation patterns, bound every string/list, normalize times to the repository's UTC format, reject non-finite progress values, and never serialize object dictionaries wholesale.

**Acceptance criteria:**

- Valid records round-trip through canonical JSON and invalid/oversized fields fail with typed bounded errors.
- Stable ordering uses updated time then record ID and is independent of adapter input order.
- Per-source and aggregate limits produce explicit truncation metadata.
- A prohibited-field corpus cannot enter the schema or serializer.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/workloads.py tests/observability/test_workloads.py`

### T002: Define the router active registry and terminal projection

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/observability/workloads.py, anvil_serving/router/workloads.py, anvil_serving/router/decision_log.py, tests/router/test_workloads.py
**Dependencies:** T001

Expose the canonical timestamp helpers needed by router projection and implement the bounded registry, initially inert token, ordered active transitions, pending terminal proposal, idempotent finalization, and active/recent projection over the existing `DecisionLog`. Add `DecisionLog.recent(limit=512)` and only the safe workload timestamps/outcome required for projection. Do not wire runtime callers in this slice and do not create a second terminal store.

**Acceptance criteria:**

- Registry transitions enforce the fixed order and reject unknown/raw owner text; inactive tokens create no record.
- Cap 1024 saturation reports the exact count of currently unrepresented active requests and changes only observability metadata, never request success, admission, or dispatch. Saturated requests remain eligible for terminal projection.
- Concurrent/repeated finalization appends at most one existing `DecisionLog` record, always clears represented active state, and terminal projection reads at most 512 recent decisions while superseding same-ID active entries.
- Malformed or unavailable decisions/projection clocks fail safely without routing effects, ghost active entries, or a second terminal collection.
- Terminal projection contains no request, response, tool, endpoint, credential, or raw-error data.

**Verification:**

- `python scripts/run_tests.py tests/router/test_workloads.py tests/router/test_decision_log.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/workloads.py anvil_serving/router/workloads.py anvil_serving/router/decision_log.py tests/router/test_workloads.py`

### T008: Wire authenticated request and streaming lifecycle boundaries

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/router/serve.py, anvil_serving/router/backends/relay.py, tests/router/test_workloads.py
**Dependencies:** T002

Instantiate the one shared registry in `build_server`, route authenticated requests through `RoutingBackend.generate_tracked`, and advance at actual alias/readiness, lease, backend-return, and first-iteration boundaries. Preserve ordinary `generate()` behavior. Classify relay timeout versus error without retaining raw exceptions, and carry pending terminal state through `RouterWorkloadStream` so the front door commits only after buffered or streaming delivery and final flush.

**Acceptance criteria:**

- Invalid authentication and unknown aliases create no workload. A valid alias rejected before admission is visible as `checking` then exactly one `rejected` terminal record.
- Blocking readiness is observable as `checking`, blocking after lease acquisition as `admitted`, return from `backend.generate` as `dispatched`, and first iterator advancement—including empty/tool-only streams—as `streaming`.
- Success, eager generate failure, upstream error, timeout, malformed SSE, cancellation, disconnect, generator close, close-before-first-iteration, buffered/SSE socket-write failure, and final-flush failure remove the active record exactly once and append at most one terminal decision.
- Streaming lifetime ends on actual delivery/close/cancellation rather than response or iterator creation. An immediate backend terminal commit is a required negative control: it must fail the buffered socket-write regression because that result must be `disconnected`, not `success`.
- Disabled observation, saturation, or registry/clock failure leaves ordinary routing, admission, response bytes, and cleanup behavior unchanged; tests exercise the actual `build_server` shared-registry path.
- No request/response/tool content, raw error, endpoint, credential, or caller label is retained.

**Verification:**

- `python scripts/run_tests.py tests/router/test_workloads.py tests/router/test_streaming_relay.py tests/router/test_request_measurements.py tests/router/test_front_door.py -x -q`
- `python -m ruff check anvil_serving/router/front_door.py anvil_serving/router/serve.py anvil_serving/router/backends/relay.py tests/router/test_workloads.py`

### T009: Harden router terminal records and lifecycle regressions

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/workloads.py, anvil_serving/router/decision_log.py, tests/router/test_workloads.py, tests/router/test_observability_hardening.py
**Dependencies:** T002, T008

Harden the registry/`DecisionLog` integration against finish, delivery, close, timeout, and disconnect races without creating a second terminal store. Prove exact fixed outcome/timestamp projection, bounded active/recent reads, and strict exclusion of caller-derived identity and content.

**Acceptance criteria:**

- Terminal outcome and timestamps map exactly from authoritative lifecycle events; raw status/error strings never become schema text.
- Repeated close/delivery/finish races create at most one terminal append and leave no active record. Removing the finalized guard is a required negative control that must produce a duplicate-append test failure.
- At cap and under concurrent active/terminal reads, the 1024 active and 512 recent bounds hold and terminal supersedes matching active projection deterministically.
- Seeded prompts, responses, tools, filenames, URLs, credentials, private addresses, and exceptions are absent from records and logs.
- Replacing the generated gateway identity with caller `request_id`, or removing the admitted-phase advance, are required negative controls that must fail identity/privacy and phase-boundary regressions respectively.
- Existing decision-log capacity, routing, and metadata-only behavior remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/router/test_workloads.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/workloads.py anvil_serving/router/decision_log.py tests/router/test_workloads.py tests/router/test_observability_hardening.py`

### T003: Add bounded controller-store workload projections

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/store.py, anvil_serving/observability/workloads.py, tests/test_benchmark_jobs.py, tests/control_plane/test_benchmark_jobs.py
**Dependencies:** T001

Add explicit `list_workloads`-style methods to controller operation and benchmark stores using their existing locks/connections and lifecycle fields. Cap rows in one owner query, copy immutable values inside the transaction/lock, and map only the fixed controller/benchmark states. Media follows in T010.

**Acceptance criteria:**

- Controller operation and benchmark stores return deterministic active/recent bounded projections from one consistent snapshot.
- Unknown future owner states remain visible as unsupported rather than being mislabeled.
- Readers never observe partially updated rows during concurrent state transitions.
- Existing controller and benchmark lifecycle tests remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py tests/observability/test_workloads.py -x -q`

### T010: Add the bounded media-store workload projection

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/media/jobs.py, anvil_serving/observability/workloads.py, tests/media/test_jobs.py, tests/observability/test_workloads.py
**Dependencies:** T001

Add a safe bounded list projection to `MediaJobStore` using its existing lock and lifecycle fields. Map accepted/queued/awaiting-approval/preparing/submitting/running/terminal states exactly; unknown future values remain unsupported with no raw text.

**Acceptance criteria:**

- One locked snapshot returns deterministic active/recent rows without N+1 reads or storage internals escaping.
- Cancelled/canceled normalization, approval, terminal outcome, and unknown-state mappings match the fixed table.
- Concurrent updates cannot expose partially changed media state.
- Media titles, paths, prompts, provider payloads, principals, and raw errors never enter the projection.

**Verification:**

- `python scripts/run_tests.py tests/media/test_jobs.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/media/jobs.py anvil_serving/observability/workloads.py tests/media/test_jobs.py`

### T004: Project recipe-managed serve workloads

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serve_recipes.py, anvil_serving/observability/workloads.py, tests/test_serve_recipes.py, tests/observability/test_workloads.py
**Dependencies:** T001

Build the read-only recipe-managed adapter from existing status results, retaining separate configuration, observation, health-plus-identity, and freshness provenance. Call existing status functions only; do not invoke Docker directly or mutate lifecycle. Manifest-managed serves follow in T011.

**Acceptance criteria:**

- Recipe workload IDs are stable and safe without exposing container IDs, commands, mounts, URLs, host paths, or recipe paths.
- Configured-only, running-observed, healthy-identity, stale, absent, and inspection-error cases project distinctly.
- Projection performs no lifecycle mutation and uses no raw Docker fallback.

**Verification:**

- `python scripts/run_tests.py tests/test_serve_recipes.py tests/observability/test_workloads.py -x -q`

### T011: Project manifest-managed serve workloads

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serves.py, anvil_serving/observability/workloads.py, tests/test_serves.py, tests/observability/test_workloads.py
**Dependencies:** T001

Build the read-only manifest-managed adapter from existing serve status results with the same provenance rules as recipes and a distinct owner identity. Do not start, stop, inspect via raw Docker, or infer running from configuration/health alone.

**Acceptance criteria:**

- Manifest and recipe records share kind `recipe-serve` but have distinct stable owner IDs.
- Configured-only, running-observed, health-plus-exact-identity, stale, absent, and inspection-error cases map exactly.
- Health without served identity never becomes `healthy-identity`.
- Container IDs, commands, mounts, URLs, paths, engine/quantization selection, and raw inspection errors are absent.

**Verification:**

- `python scripts/run_tests.py tests/test_serves.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/serves.py anvil_serving/observability/workloads.py tests/test_serves.py`

### T005: Add the authenticated router workload endpoint

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/observability/workloads.py, tests/router/test_operational_endpoints.py, tests/router/test_front_door_auth.py
**Dependencies:** T009

**External prerequisites:** fleet-node-enrollment:T008, fleet-node-enrollment:T009, and fleet-node-enrollment:T010 must each be `done` before this task is claimed. Anvil's PRD parser supports local dependency IDs only; the coordinator must run `anvil show fleet-node-enrollment:T008 --prd fleet-node-enrollment --json`, `anvil show fleet-node-enrollment:T009 --prd fleet-node-enrollment --json`, and `anvil show fleet-node-enrollment:T010 --prd fleet-node-enrollment --json`, require `.data.task.status == "done"` for every result, and retain all results in the dispatch packet. A missing task is an unmet prerequisite, not permission to proceed.

Expose authenticated `GET /v1/workloads` on the router using the canonical query/serializer, the registry already created by `build_server`, and the dedicated `workloads:read` gate. Inject the trusted configured host identifier into `source_result(host, query, now)` for canonical ID construction; never infer it from bind address, machine hostname, or caller input and never construct a second registry. This slice is router-local only; node aggregation and fleet fan-out follow.

**Acceptance criteria:**

- Data-plane, legacy, media-only, missing-policy, and wrong-scope credentials are denied before registry/DecisionLog reads.
- Unknown/repeated scalar filters, out-of-range windows/limits, malformed times, and unsupported kinds/states fail with fixed safe errors.
- Valid queries return exact `anvil-workloads/v1` canonical bytes and explicit truncation metadata.
- Endpoint success and errors contain no request content, route endpoint, token, raw response, or exception.

**Verification:**

- `python scripts/run_tests.py tests/router/test_operational_endpoints.py tests/router/test_front_door_auth.py tests/router/test_workloads.py -x -q`

### T012: Aggregate node-local workload sources

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/controller/server.py, anvil_serving/observability/probes/remote_controller.py, tests/test_controller.py, tests/observability/test_remote_controller.py
**Dependencies:** T003, T004, T005, T010, T011

Add the controller workload operation that projects its own controller/benchmark/media/managed-status sources and fetches router work only through the topology-declared authenticated loopback resource. Enforce `workloads:read` before any source read and keep a status for every source.

**Acceptance criteria:**

- Missing source/config/auth is unavailable; one surviving source makes the node partial; only all-source failure makes it unavailable.
- Malformed schema/record, expected-node mismatch, or source time over 30 seconds in the future fails only that source.
- Source time and collection time remain distinct; stale quality uses source observation age.
- Controller never opens router storage, guesses a port, uses SSH, or returns token/address/path/raw-response/error content.

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/observability/test_remote_controller.py -x -q`
- `python scripts/run_tests.py tests/test_controller_token_normalization.py -x -q`

### T013: Add bounded expected-node fleet fan-out

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/transports.py, anvil_serving/observability/workloads.py, tests/observability/test_remote_controller.py, tests/observability/test_workloads.py
**Dependencies:** T012

Collect only declared expected-node controller workload operations with at most four concurrent calls, a two-second per-node deadline, a five-second collection deadline, and a visible result for every declared node. Do not queue unbounded work or retry via SSH.

**Acceptance criteria:**

- Wrong-node, timeout, sleeping, unreachable, incompatible-schema, malformed-record, and future-clock nodes remain explicit entries.
- Healthy node records survive other-node/source failures and aggregate completeness/truncation are correct.
- Collection returns by the aggregate deadline even when node count exceeds concurrency; every omitted/unstarted node is explicit.
- No address, token, raw response, transport dictionary, or exception crosses the canonical envelope.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_remote_controller.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/transports.py anvil_serving/observability/workloads.py tests/observability/test_remote_controller.py`

### T006: Register router and fleet workload CLI commands

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/cli.py, anvil_serving/commands/router.py, anvil_serving/commands/fleet.py, tests/test_cli.py
**Dependencies:** T013

Register `router workloads` and `fleet workloads` CLI parsers and dispatch backed by the canonical router/fleet query APIs. Keep identical filters, return structured dictionaries below the wrapper, and derive JSON/text from the same result. Controller/MCP and generated manifest work follow.

**Acceptance criteria:**

- Both commands support the same bounded filters and return schema-equivalent records and truncation/freshness metadata.
- Unknown/repeated filters and unsafe values fail before controller/router calls.
- Workload surfaces expose no mutation verbs or callbacks.
- Help and dispatch expose only the two read-only commands.

**Verification:**

- `python scripts/run_tests.py tests/test_cli.py -x -q`
- `python -m anvil_serving.cli router workloads --help`
- `python -m anvil_serving.cli fleet workloads --help`

### T014: Register read-only controller and MCP workload tools

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/server.py, anvil_serving/control_plane/mcp/tools/__init__.py, anvil_serving/control_plane/mcp/tools/workloads.py, tests/control_plane/test_controller_chaining.py
**Dependencies:** T012, T013

Register the node/fleet read-only operations and MCP tools over the same canonical query/result. Require `workloads:read` at the controller boundary and expose no mutation callback, raw transport context, or alternate serializer.

**Acceptance criteria:**

- Controller and MCP return schema-equivalent canonical records for the same query.
- Unauthenticated, legacy, media-only, and wrong-scope principals are denied before collection.
- Tool schemas have only the reviewed filters and contain no operation capable of mutation.
- Errors and chaining context contain no endpoint, credential, raw response, path, or exception.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_chaining.py tests/test_controller.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/server.py anvil_serving/control_plane/mcp/tools tests/control_plane/test_controller_chaining.py`

### T015: Generate workload command-manifest and surface parity

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/commands/spec.py, docs/CLI-COMMAND-MANIFEST.json, tests/test_command_tree.py, tests/router/test_operational_endpoints.py
**Dependencies:** T005, T006, T014

Declare the workload commands/operations and regenerate the command manifest through repository helpers. Add parity tests proving router endpoint, CLI, controller, MCP, and manifest share filters, schema version, limits, and read-only authority.

**Acceptance criteria:**

- Generated manifest and help contain `router workloads` and `fleet workloads` with only reviewed filters and no mutation fields.
- Equivalent queries return canonical-equivalent records/truncation/freshness across surfaces.
- Parser/manifest/endpoint drift and an extra free-text or callback field fail tests.
- Regeneration is deterministic and introduces no hand-edited manifest divergence.

**Verification:**

- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `python scripts/run_tests.py tests/test_command_tree.py tests/router/test_operational_endpoints.py -x -q`
- `git diff --check`

### T007: Add the canonical workload dashboard panel

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/observability/dashboard/app.py, anvil_serving/observability/dashboard/static/index.html, tests/observability/test_dashboard.py, tests/observability/test_status_redaction.py
**Dependencies:** T013

Render active/recent work grouped by node and kind with clear stale, partial, unavailable, and truncated states. Consume only the canonical node/fleet API schema. Preserve existing no-build dashboard idioms, escape all labels, avoid client-generated HTML, and test expected safe fields plus prohibited seeded values.

**Acceptance criteria:**

- Dashboard makes idle, unavailable, stale, partial, active, terminal, and truncated states visually distinct in accessible text.
- UI data and node/fleet API data are schema-equivalent.
- Seeded markup, credentials, paths, URLs, private addresses, payloads, and raw exceptions are absent or escaped in rendered HTML and logs.
- Dashboard failure does not alter collection, routing, admission, or workload lifecycle.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard.py tests/observability/test_status_redaction.py tests/observability/test_workloads.py -x -q`

### T016: Document workload visibility and run whole-PRD gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI.md, docs/ARCHITECTURE.md, tests/test_cli_reference_audit.py, tests/test_docs_command_invocations.py
**Dependencies:** T007, T015

Document the canonical schema, authorization, ownership, provenance, filters, partiality, freshness, caps, exclusions, and troubleshooting. Add final documentation/command invocation regressions and run the whole-PRD release gates; do not claim completion from focused slices alone.

**Acceptance criteria:**

- Docs distinguish configuration, recorded state, observed running, health-plus-identity, stale, absent, unavailable, and unsupported.
- Docs state that all surfaces require `workloads:read` and never expose payloads, paths, network identities, credentials, or mutation authority.
- CLI/API/MCP/dashboard examples use only generic safe values and match generated command/schema contracts.
- Focused, full, lint, strict-doc, link, command-manifest, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workloads.py tests/observability/test_dashboard.py tests/router/test_workloads.py tests/test_controller.py -x -q`
- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `git diff --check`
