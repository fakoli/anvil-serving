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

- R001: Define a versioned immutable `WorkloadRecord` with bounded fields for record ID, workload kind, owner component, safe host ID, safe route/resource ID, display label, state, phase, created/started/updated/finished timestamps, progress summary, terminal outcome code, provenance, freshness, and optional parent correlation ID.
- R002: The first schema must support exactly the kinds `router-request`, `controller-operation`, `benchmark-job`, `media-job`, and `recipe-serve`; unknown kinds or states must be preserved as typed unsupported records or rejected at the owning adapter, never guessed into a known lifecycle.
- R003: Each source adapter must read the authoritative subsystem state and project it into `WorkloadRecord`; the unified registry must not become a second source of truth or write state back to the owner.
- R004: The router must create an active metadata record only after front-door authentication and alias validation, then advance it through bounded states `checking`, `admitted`, `dispatched`, `streaming`, and `terminal` using the existing request/stream lifecycle.
- R005: Router records must be removed from the active registry on every success, error, timeout, cancellation, client disconnect, and stream close; one bounded terminal record must be available through the existing content-free decision/telemetry path.
- R006: `OperationStore`, `BenchmarkJobStore`, and `MediaJobStore` must expose bounded list/recent projection methods with deterministic ordering, pagination/limit ceilings, and transaction-safe snapshots rather than permitting workload code to query their SQLite tables directly.
- R007: Managed recipe serves must be projected from Anvil-owned recipe/container state and stable labels, distinguish recipe-owned from manifest-owned serves, and report configured, observed, stale, absent, or error provenance without equating container existence with a healthy serving identity.
- R008: Remote collection must use the authenticated `ControllerTransport` with `expected_node`; a response with wrong node identity, incompatible schema, invalid record, timeout, or unreachable controller must become a per-node unavailable/error result rather than disappearing or aborting other nodes.
- R009: A declared sleeping or unreachable host must remain visible as an unavailable node summary, and the fleet result must report `complete`, `partial`, or `unavailable` with collection timestamps and freshness instead of presenting missing hosts as idle.
- R010: All projections and errors must use explicit allowlists and exclude prompts, messages, response bodies, tool arguments/results, audio/image/video paths or URLs, endpoint URLs, private addresses, credentials, auth headers, environment values, local personal paths, raw commands, raw SQL, and raw exceptions.
- R011: `router workloads`, `fleet workloads`, one authenticated router/controller JSON endpoint, MCP/controller tools, and the observability dashboard must serialize the same schema and filtering semantics; no surface may silently add sensitive fields.
- R012: Queries must support bounded filters for kind, state, safe host ID, owner, and active/recent window; results must use stable newest-updated ordering with record-ID tie breaks, include truncation metadata, and apply hard per-source and aggregate limits.
- R013: Type-specific mutation commands remain the only mutation authority; workload records may include safe links or identifiers for those commands, but the unified API and dashboard must be read-only.
- R014: Workload data must never feed router choice, admission, serve lifecycle, controller execution, promotion, or automated remediation; implementation must remain stdlib-only, return structured values from library code, and use injected clocks for deterministic tests.

## Acceptance Criteria

- The same fixture records serialize byte-equivalently through local CLI JSON, authenticated endpoint, controller/MCP result, remote fleet aggregation, and dashboard API.
- Router tests cover ordinary success, rejection before admission, upstream HTTP error, timeout, cancellation, disconnect, normal SSE completion, and malformed SSE; active records always return to zero and exactly one safe terminal projection remains where policy allows.
- Controller operation, benchmark, and media stores return deterministic bounded snapshots without direct cross-module SQL access; concurrent writer/reader tests never expose partial rows.
- Recipe projections distinguish configured, observed-running, healthy-identity, stale, absent, and inspection-error states without claiming deployment or qualification.
- A fleet query with one healthy node, one wrong-identity node, and one unreachable/sleeping node returns the healthy records plus explicit error rows and overall `partial` status.
- Redaction/adversarial tests seed every prohibited field and prove none appear in JSON, text, logs, endpoint errors, or dashboard bootstrap data.
- Every query surface enforces filters, stable ordering, freshness, per-source/aggregate limits, and truncation metadata.
- Focused tests, full tests, lint, strict docs, link checks, command-manifest checks, and diff checks pass.

## Risks

- A unified registry can accidentally become duplicate mutable state and drift from owner stores; projections must remain read-only and derived.
- Router lifecycle cleanup is easy to miss on disconnect and SSE generator close paths, causing ghost active requests or memory growth.
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

## Code Map

- `anvil_serving/observability/schema.py::TelemetrySample` and `anvil_serving/observability/api.py::TelemetryRegistry` provide immutable schema, bounded registry, injected-clock, and API serialization patterns. Workloads should be a sibling projection module, not fields bolted onto hardware telemetry.
- `anvil_serving/router/front_door.py`, `serve.py::RoutingBackend`, `backends.py`, and `decision_log.py::DecisionLog` own request authentication, alias validation, dispatch, streaming lifetime, and content-free terminal records.
- `anvil_serving/router/router_telemetry.py` shows how bounded aggregates derive from `DecisionLog`; do not add prompts or raw records to make workload visibility easier.
- `anvil_serving/control_plane/controller/store.py::BenchmarkJobStore` and `OperationStore` own durable controller job state. Add public bounded projection methods next to existing locked/transactional reads.
- `anvil_serving/media/jobs.py::MediaJobStore` owns media-job lifecycle and cancellation; workload visibility must call a safe list projection rather than access storage internals.
- `anvil_serving/serve_recipes.py` and `serves.py` distinguish recipe-managed and manifest-managed state, container observation, health, model identity, and lifecycle ownership.
- `anvil_serving/transports.py::ControllerTransport` and `anvil_serving/observability/probes/remote_controller.py::collect_remote_telemetry` provide expected-node, remote schema, deadline, and partial-failure patterns for fleet collection.
- `anvil_serving/observability/dashboard/app.py` and its static/template modules own the existing local dashboard; add a workload panel without a parallel server or client-only schema.
- `anvil_serving/commands/spec.py::write_manifest`, CLI modules, and controller tool registration own user-visible command/MCP surfaces and generated documentation.
- Primary tests are `tests/router/test_front_door.py`, `tests/router/test_streaming_relay.py`, `tests/router/test_decision_log.py`, `tests/router/test_observability_hardening.py`, `tests/test_controller.py`, `tests/test_benchmark_jobs.py`, `tests/media/test_jobs.py`, `tests/test_serve_recipes.py`, `tests/observability/test_remote_controller.py`, `tests/observability/test_api.py`, `tests/observability/test_dashboard.py`, and `tests/observability/test_status_redaction.py`.

## Features

### F001: Canonical workload projection schema

Define bounded records, source adapters, filtering, ordering, freshness, limits, and safe serialization without duplicating owner state.

**Requirements:** R001, R002, R003, R010, R012, R013, R014

### F002: Router active and recent workload lifecycle

Track authenticated valid-alias requests through dispatch/stream termination and project safe terminal metadata.

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

### T002: Track router requests through every terminal path

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/router/serve.py, anvil_serving/router/backends/relay.py, anvil_serving/router/backends/sse.py, anvil_serving/router/decision_log.py, anvil_serving/observability/workloads.py, tests/router/test_workloads.py, tests/router/test_streaming_relay.py
**Dependencies:** T001

Add a bounded in-memory active registry after authentication and alias validation, advance it from existing lifecycle events, and emit/remove records with exactly-once finalization. Correlate to a safe generated request ID and selected logical route/member metadata only. Executor guidance: model finalization as an idempotent close token/context manager, attach it to generator close callbacks, use injected clocks/IDs, and write backend fakes that exercise disconnect/cancellation rather than relying only on status responses.

**Acceptance criteria:**

- Invalid auth and unknown alias requests create no workload record.
- Every documented lifecycle state appears at the correct boundary.
- All ordinary and streaming terminal cases remove the active record exactly once.
- Terminal projection contains no request, response, tool, endpoint, credential, or raw-error data.

**Verification:**

- `python scripts/run_tests.py tests/router/test_workloads.py tests/router/test_streaming_relay.py tests/router/test_decision_log.py -x -q`
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q`

### T003: Add bounded workload projections to authoritative stores

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/store.py, anvil_serving/media/jobs.py, anvil_serving/observability/workloads.py, tests/test_benchmark_jobs.py, tests/control_plane/test_benchmark_jobs.py, tests/media/test_jobs.py, tests/observability/test_workloads.py
**Dependencies:** T001

Add explicit `list_workloads`-style methods to operation, benchmark, and media stores using their existing locks/connections and lifecycle fields. Map owner states through small adapter functions and retain unknown states visibly. Executor guidance: do not import SQLite connections across modules or perform N+1 lookups; cap rows in the owner query, copy into immutable values inside the transaction/lock, and test concurrent snapshots with events instead of sleeps.

**Acceptance criteria:**

- Each store returns deterministic active/recent bounded projections from one consistent snapshot.
- Unknown future owner states remain visible as unsupported rather than being mislabeled.
- Readers never observe partially updated rows during concurrent state transitions.
- Existing owner lifecycle and cancellation tests remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py tests/media/test_jobs.py tests/observability/test_workloads.py -x -q`

### T004: Project managed recipe and manifest serve workloads

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serve_recipes.py, anvil_serving/serves.py, anvil_serving/observability/workloads.py, tests/test_serve_recipes.py, tests/observability/test_workloads.py
**Dependencies:** T001

Build read-only adapters from existing managed status results, retaining separate configuration, observed container/process, health, exact served identity, and freshness provenance. Identify recipe-owned versus manifest-owned resources with stable safe IDs. Executor guidance: call existing status functions and return their structured results; do not invoke Docker directly from workload code, do not start/stop anything, and do not collapse absent inspection evidence into idle.

**Acceptance criteria:**

- Recipe and manifest workloads are distinguishable without exposing container IDs, commands, mounts, URLs, or host paths.
- Configured-only, running-observed, healthy-identity, stale, absent, and inspection-error cases project distinctly.
- Projection performs no lifecycle mutation and uses no raw Docker fallback.

**Verification:**

- `python scripts/run_tests.py tests/test_serve_recipes.py tests/observability/test_workloads.py -x -q`

### T005: Add authenticated node collection and partial fleet aggregation

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/controller/server.py, anvil_serving/transports.py, anvil_serving/observability/probes/remote_controller.py, anvil_serving/observability/workloads.py, tests/test_controller.py, tests/observability/test_remote_controller.py, tests/observability/test_workloads.py
**Dependencies:** T002, T003, T004

Expose one bounded authenticated controller operation returning the canonical node workload result. Aggregate only topology-declared nodes through `ControllerTransport(expected_node=...)` with per-node deadlines and bounded parallelism, preserving errors and sleeping/unreachable nodes. Executor guidance: reuse remote telemetry envelope/identity validation, never fall back to SSH, sort after collection, and keep one node's timeout from cancelling successful peers.

**Acceptance criteria:**

- Wrong-node, incompatible-schema, malformed-record, timeout, sleeping, and unreachable results remain explicit per-node entries.
- Healthy node records survive partial failures and aggregate status is correct.
- Fan-out respects per-node and aggregate deadlines and hard result limits.
- No remote address, token, raw response, or exception is returned.

**Verification:**

- `python scripts/run_tests.py tests/test_controller.py tests/observability/test_remote_controller.py tests/observability/test_workloads.py -x -q`
- `python scripts/run_tests.py tests/test_controller_token_normalization.py -x -q`

### T006: Register CLI, router endpoint, and MCP/controller workload surfaces

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/cli.py, anvil_serving/router/front_door.py, anvil_serving/commands/spec.py, anvil_serving/control_plane/controller/server.py, docs/CLI-COMMAND-MANIFEST.json, tests/router/test_operational_endpoints.py, tests/test_cli.py, tests/control_plane/test_controller_chaining.py
**Dependencies:** T005

Add `router workloads` and `fleet workloads`, the authenticated bounded endpoint, and read-only controller/MCP operations backed by the same query/serializer. Regenerate the command manifest through repository helpers. Executor guidance: keep filters identical across surfaces, require existing auth, return structured dictionaries in handlers, print only in CLI wrappers, and add parity tests comparing parsed JSON rather than formatting.

**Acceptance criteria:**

- Every surface supports the same bounded filters and returns schema-equivalent records and truncation/freshness metadata.
- Router/controller endpoints reject unauthenticated requests using existing front-door behavior.
- Workload surfaces expose no mutation verbs or callbacks.
- Generated command manifest and help output include the new read-only commands.

**Verification:**

- `python scripts/run_tests.py tests/router/test_operational_endpoints.py tests/test_cli.py tests/control_plane/test_controller_chaining.py -x -q`
- `python -m anvil_serving.cli router workloads --help`
- `python -m anvil_serving.cli fleet workloads --help`

### T007: Add the dashboard panel, documentation, and adversarial gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/observability/dashboard/app.py, anvil_serving/observability/dashboard/static/index.html, docs/CLI.md, docs/ARCHITECTURE.md, tests/observability/test_dashboard.py, tests/observability/test_status_redaction.py
**Dependencies:** T006

Render active/recent work grouped by node and kind with clear stale, partial, unavailable, and truncated states. Consume only the canonical API schema. Document authority, provenance, exclusions, filters, and troubleshooting. Executor guidance: preserve existing no-build dashboard idioms, escape all labels, avoid client-generated HTML, and test rendered output for both expected safe fields and prohibited seeded values.

**Acceptance criteria:**

- Dashboard makes idle, unavailable, stale, partial, active, terminal, and truncated states visually distinct in accessible text.
- UI data and CLI/API data are schema-equivalent.
- Redaction tests cover JSON, text CLI, endpoint errors, logs, and rendered HTML.
- Focused, full, lint, strict-doc, link, command-manifest, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard.py tests/observability/test_status_redaction.py tests/observability/test_workloads.py -x -q`
- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `git diff --check`
