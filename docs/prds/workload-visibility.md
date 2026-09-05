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
- Managed-serve projections distinguish configured, observed-running, stale, absent, unsupported and inspection-error states without claiming deployment or qualification; neither recipe nor manifest source emits healthy-identity in v1 because neither has an authoritative container-bound served-identity observation.
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
- Router active registry cap is 1024. Saturation keeps exact internal per-state counts of currently unrepresented active requests but never affects request success. Anonymous counts have no timestamps, so a query that could include them reports `omitted = null` and partial status, not a fabricated exact freshness-filtered count. Owner/kind/host/state exclusions that prove no matching anonymous request retain exact zero. A saturated token may still yield terminal `DecisionLog` projection. Track finalization-in-flight only with bounded per-state counters, moving out of active-unrepresented accounting before terminal append and clearing in finally; any query that could match its previous active state or terminal state reports unknown omission during that window. No identity collection or lock across the sink is added. Recent terminal projection uses `DecisionLog.recent(limit=512)` and never a second terminal store. Terminal entries supersede same-ID active entries.
- `DecisionRecord` may add only exact UTC microsecond-Z workload timestamps and the fixed workload outcome. The internally generated `gateway_request_id` is the owner-native identity; caller request/workbench/task IDs and payload-derived values are never workload identity or projected fields. `source_result(host, query, now)` receives the trusted configured host from the later endpoint/collection seam, derives the canonical digest there, and never infers host identity from bind address, machine hostname, or caller input.
- Provenance has separate `source_authority` (`router-memory|controller-store|benchmark-store|media-store|managed-status`) and `observation_quality` (`recorded|configured|observed-running|healthy-identity|stale|absent|inspection-error`). Health-only never maps to healthy-identity. Both recipe/manifest serves use kind `recipe-serve` with their distinct owner.
- Each node response identifies its expected node and every record must match it. Preserve source time and collection time; a remotely received source containing timestamps more than 30 seconds in the future fails that source. Before emitting its own source, the local router producer validates each constructed record against collection time, quarantines invalid/future entries and returns trustworthy survivors with partial status and a fixed typed error. Do not weaken received-wire validation. Freshness uses source observation age (30-second default stale threshold), never collection time relabeled as observation.
- Node controller reads its own stores through bounded public projection methods and router active/recent state through authenticated `GET /v1/workloads` at a topology-declared loopback router resource. Missing source/config/auth is explicit unavailable, not idle. It must not open the router database or discover ports.
- Fleet queries only expected-node controller workload operations, with at most four concurrent calls, 2-second per-node and 5-second collection deadlines, no unbounded queued work, and a visible result for every declared node. Failed or sleeping nodes remain visible. Dashboard consumes this same node/fleet result; it never reconstructs records from DOM or hardware samples.
- All unified reads require a per-client `workloads:read` operator grant. Router data-plane bearer tokens and media-only principals are not implicitly workload operators. The shared authorization prerequisite is fleet-node-enrollment:T008; denial occurs before collection, including router/controller/MCP/dashboard.
- Canonical record bytes are shared across surfaces. Envelopes add node/source status, collection time, completeness, and truncation; never pass generic command/transport context through them. Whole-envelope adversarial tests seed credentials, user paths, tool payloads, URLs, private addresses and raw exceptions.
- Every node envelope includes per-source `complete|partial|unavailable` status. A malformed/future received router source becomes unavailable without discarding healthy store/serve sources; any surviving source makes node status partial, and only all-source failure makes it unavailable. A valid locally produced partial router source retains its safe surviving records and partial status. Fleet retains that node status.

### Recipe workload observation and projection contract

T004.1 owns a bounded metadata-only producer; T004 consumes that producer and never invokes Docker, reads a registry, or invents health/identity evidence itself.

- Add frozen `RecipeConfiguredObservation(recipe_digest: str, configured_at: datetime, observed_at: datetime)`, `RecipeContainerObservation(container_id: str, recipe_digest: str | None, state: WorkloadState, created_at: datetime, updated_at: datetime, observed_at: datetime)`, `RecipeComponentResult(status: ResultStatus, observed_at: datetime | None, records: tuple[RecipeConfiguredObservation | RecipeContainerObservation, ...], omitted: int | None, error: WorkloadErrorCode | None)`, and `RecipeWorkloadSnapshot(configuration: RecipeComponentResult, runtime: RecipeComponentResult)`. Error values are absent or exactly `invalid-workload|future-workload-timestamp|workload-source-unavailable`; omitted is nonnegative or null when matching completeness is unknowable. Complete requires error absent and omitted zero; unavailable requires no records and a fixed error.
- `capture_recipe_workload_snapshot(registry_path, *, clock, _capture=None)` is the sole T004.1 producer. It reads the configured registry path without mutation, uses `fstat` on the open handle before and after the read, reads at most 8 MiB plus one sentinel byte before TOML materialization, and preserves legacy `load_registry` and `discover_recipe_containers` return contracts unchanged.
- Runtime observation uses an owner-controlled private seam that lazily reuses `controller_diagnostics._capture_fixed_child` with its fixed local-Docker environment, 256-KiB capture ceiling and ten-second deadline. The two fixed commands list at most 256 managed IDs and inspect them through a Go template containing only full container ID, the exact recipe-management and recipe-digest labels, `Created`, and `State.Status|Running|StartedAt|FinishedAt`. It never captures Args, Cmd, Env, mounts, URLs, ports, health or served-model arguments and is not an adapter-level raw-Docker fallback.
- Capture `observed_at` immediately after each successful component read. Registry `mtime_ns`, converted with integer microseconds, is configuration evidence only and supplies configured `created_at == updated_at`; source freshness uses the distinct component `observed_at`. Docker UTC RFC3339Nano values are validated exactly and sub-microsecond digits are floored deterministically into canonical UTC microseconds. Running uses Docker `Created` and `StartedAt`; absent uses `Created` and the latest valid nonzero `StartedAt|FinishedAt`, falling back to `Created`; neither lifecycle timestamp becomes source observation time.
- Require exact booleans and coherent fixed states: `Running is True` with `Status == running` is observed running; `Running is False` with `created|exited|dead` is absent; another exact state is unsupported with inspection-error quality; a wrong type or inconsistent pair is malformed. Health and configured served identity are ignored, so the recipe source can never emit `healthy-identity`.
- Each component retains at most 256 observations. Overflow, malformed members, timestamp violations, timeout and capture/read failure affect only that component and preserve trustworthy peers. Configuration and runtime failures remain separate in the snapshot; raw exceptions, paths, labels and rejected values are never retained.
- `list_recipe_workloads(registry_path, host, query, now, *, snapshot_reader=capture_recipe_workload_snapshot) -> SourceResult` is the T004 adapter. Config identity is native `recipe-config:<semantic recipe digest>`; observed identity is native `recipe-container:<validated full container ID>`; pass those exact owner-generated values only to canonical `workload_id`.
- An observed row suppresses a configured row only when its exact recipe digest matches; every real matching container remains a distinct observed row, and a missing or unknown digest suppresses nothing. Map configured to `configured/configured/configured`, running to `running/running/observed-running`, stopped to `absent/absent/absent`, and unknown to `unsupported/unsupported/unknown/inspection-error`. When source age exceeds the canonical threshold, retain the supported state with stale quality; active-only excludes it.
- Add one canonical `select_managed_records` helper bounded to 512 recipe/manifest candidates that applies all canonical filters and stable ordering before the source cap of `min(query.limit, 200)`; T011 reuses it. Validate each candidate independently with `validate_source_records` before selection, quarantine invalid/future peers, then validate the final source. Complete producer data reports exact query omissions; any producer overflow/malformed/failure reports omitted null. Fixed final error precedence is `invalid-workload`, then `future-workload-timestamp`, then `workload-source-unavailable`; one surviving component yields partial, and both failed with no trustworthy records yields unavailable. Empty successful components are complete, never inferred idle or failure.

### Manifest workload observation and projection contract

T011.1 owns the bounded observation-only producer in manifest_workloads.py;
T011 projects only its immutable snapshot. Neither calls load_manifest or
status_summary: launch validation checks referenced files and rejects native
runtime, while status has no authoritative observation/identity timestamps.

- Add ManifestRuntimeKind with docker-compose, docker-generic and native. Frozen ManifestConfiguredObservation has config_digest: str, runtime: ManifestRuntimeKind, configured_at: datetime and observed_at: datetime. Frozen ManifestRuntimeObservation has config_digest: str, container_id: str | None, state: WorkloadState, created_at: datetime, updated_at: datetime and observed_at: datetime. ManifestComponentResult and ManifestWorkloadSnapshot mirror the recipe component/two-component structure with these record types, at most 256 rows each, fixed errors, and unknown omissions for incomplete capture. Retain no raw names, commands, paths, labels, capture results or exceptions in returned values or repr.
- capture_manifest_workload_snapshot(manifest_path, *, clock, _capture=None) discovers only the explicit manifest and non-link regular serves*.toml siblings in its directory. Stream at most 4096 directory entries and 64 selected files; enumeration overflow fails configuration unavailable rather than selecting an arbitrary subset. Sort the bounded selected paths lexically. Read at most 8 MiB aggregate plus one sentinel before TOML parsing, through one handle per file with matching lstat/fstat regular-file identity and before/after stable metadata; reject symlinks/reparse files. Per-file failures preserve previously valid files as partial. Missing/unreadable explicit input is an error, not silent complete-empty.
- Read only top-level serve as an exact list of exact tables, at most 256 declarations across files. Required observation fields are exact bounded ASCII name/runtime strings; Docker also needs container. Names/containers are 1-64 characters matching [A-Za-z0-9][A-Za-z0-9_.-]*. Runtime uses existing strip/lower normalization to docker or native. Native forbids container; unknown runtime is invalid. Model, port, reservation and referenced paths are neither required nor inspected: configured means declared identity, never launchable or qualified. Unknown bounded TOML fields are ignored, never retained.
- config_digest hashes canonical compact ensure_ascii JSON of ["manifest-config/v1", normalized declared runtime, name, container-or-empty]. It identifies a declared slot, not launch content or a recipe. File mtime_ns floors to UTC microseconds as configured_at; observed_at comes from the injected clock immediately after stable reading. Use the recipe time/skew rules; future/malformed peers are quarantined. No observation time is fabricated from mtime.
- Parse up only to establish explicit Compose ownership. Absent/empty up is a read-only mirror; other up values must be exact strings bounded to 8192 UTF-8 bytes and 128 shlex tokens. The only supported grammar starts docker compose or docker-compose, then -f|--file, --profile and -p|--project-name options (separate value or long-option=value), then exact up, optional -d|--detach, exactly one safe service token and EOF. Require at least one file. Each option value is a nonempty non-option token of at most 1024 UTF-8 bytes without controls; service/profile/project IDs use the same 1-64 ASCII identifier bound. Forbid duplicate project option, unknown flags/commands, missing values, multiple services and trailing arguments. Do not open/resolve file values or substitute environment variables. Unsupported syntax stays docker-generic and is never executed.
- Stack defaults to serving and uses serves.DEFAULT_STACK/_STACK_RE with an additional 64-character observation bound; derive project only through serves._stack_project. An explicit project must match it. Group identical normalized (runtime,name,container) declarations; identical up/stack mirrors collapse. One supported lifecycle owner may supersede absent-up mirrors only when stack agrees. Differing nonempty token sequences/stack, competing unsupported commands, one name bound to multiple containers, or one container bound to multiple names quarantine every affected declaration; valid unrelated peers survive. These stricter observation conflicts do not change lifecycle loader behavior.
- Runtime collection inspects only sorted exact supported declared Compose container names (maximum 256), never host-wide enumeration. Use one fixed docker inspect --type container --format TEMPLATE invocation. TEMPLATE has exactly id, name, created_at, status, running, started_at, finished_at, project and service from Docker Id, Name, Created, State and the two Compose ownership labels. No Args/Cmd/Env/mounts/ports/health/model fields. Match exact name after removing one leading Docker slash, exact project/service and a lowercase 64-hex ID before retaining a row; name/label reuse mismatch never becomes running.
- Reuse controller_diagnostics._capture_fixed_child lazily with existing local-Docker environment, 256-KiB cap and ten-second execution deadline plus bounded cleanup. Add optional internal retain_stdout_on_error=False: default behavior remains byte-for-behavior unchanged. When explicitly true, only a fully completed, cleanup-successful nonzero child may return its bounded stdout with state unavailable; discard stderr. Timeout, overflow, read failure and cleanup failure still discard all bytes. No public CLI option or raw error output is added. This lets one absent declared container avoid erasing valid complete peer rows from the same inspection.
- The producer requires exact ChildCapture/type/byte bounds, accepts complete newline-delimited JSON rows only, rejects duplicate keys/unknown fields and limits traversal to 256 rows. For successful capture, missing/duplicate/unmatched/malformed rows make runtime partial with unknown omissions. For completed nonzero capture, retain only independently valid rows as partial with unavailable error; no rows means unavailable. Failed/truncated capture cannot become absence. Never parse stderr or reflect its contents.
- Coherent running/True maps running; created|exited|dead with False maps absent; coherent paused|restarting|removing maps unsupported; unknown textual states map unsupported, but wrong types or contradictory known Boolean/state pairs are invalid. Use strict recipe RFC3339Nano parsing and source ordering: Created and StartedAt for running; Created and latest nonzero StartedAt/FinishedAt for absent; source observation is clock time immediately after bounded capture, not lifecycle time. Unsupported generic/native declarations produce unsupported runtime observations with no container ID and the configuration timestamps; no subprocess is needed when no supported Compose owners exist. No health/model probe is performed and healthy-identity is impossible.
- list_manifest_workloads(manifest_path, host, query, now, *, snapshot_reader=capture_manifest_workload_snapshot) returns SourceResult through the canonical validator/selector. A validated runtime row suppresses only its exact config_digest. Config native identity is manifest-config:<digest>; runtime identity with a real full container ID is manifest-container:<ID>; unsupported observations without ID retain the configured native identity. WorkloadOwner.MANIFEST shares recipe-serve kind, never recipe ownership.
- Map configured/running/absent/unsupported and freshness exactly as the recipe contract. Validate every candidate independently before reconciliation/selection, at most 512 total. Apply all canonical filters and newest-updated/digest ordering before min(query.limit,200). Preserve fixed error precedence invalid-workload, future-workload-timestamp, workload-source-unavailable; partial producer data has omitted null. Failed runtime plus valid configuration is partial with configured rows, not false idle/absence. Empty successful configuration with no owners is complete-empty.
- Producer/projection tests cover bounds and sentinel reads, unstable/link files, minimal native/Compose declarations, narrow grammar and mirrors/conflicts, exact declared-name argv, completed-nonzero capture preserving valid peers, malformed/missing/name-reused rows, lifecycle versus observation times and future peers, no forbidden fields or launch/health calls, all source states, filtering-before-cap and honest omissions. No live Docker or host operation is part of these tests.

### Router workload endpoint ownership and wire contract

T005 closes the startup ownership seam as part of the endpoint, not as a
caller-supplied query override.

- Add optional ServerConfig.workload_host, configured only by
  [server].workload_host. Require an exact string matching the canonical
  workload host grammar [A-Za-z][A-Za-z0-9_-]{0,63}; malformed declarations
  fail with fixed ConfigError prose and no input echo. Absent remains None
  and leaves ordinary routing/authentication unchanged. Do not infer a host
  from bind address, OS hostname, environment, topology discovery or request.
- build_server forwards that host and the existing exact shared registry to
  make_server/_make_handler through workload_host/workload_registry keywords.
  Forward its injected workload_clock as the collection clock too, defaulting
  to UTC now. No second registry, new command-line host flag or new state owner.
- Reserve exact GET /v1/workloads as a built-in workloads:read OperatorRoute.
  The injected route registry cannot replace this path. Reuse the existing
  scope-before-body/query/callback gate, bodyless framing, bounded operator
  semaphore and no-store response. Missing host/registry is a fixed HTTP503
  workload_source_unavailable after authorization, without reading the registry.
- Decode at most8192 ASCII query bytes and seven pairs with strict percent
  escapes, strict UTF-8, keep_blank_values=True and max_num_fields=7; reject
  duplicate/unknown keys. Convert only active_only=true|false to bool and
  limit/recent_seconds ASCII decimal strings to exact ints (maximum5 digits),
  then call parse_workload_query for the shared bounds/enums. No timestamp
  query or alternate spelling is added. Invalid queries return HTTP400 with
  fixed invalid_workload_query and input-free prose before source reads.
- Call registry.source_result(configured_host, query, collection_time) once
  after authorization/validation. Return node_result_to_json for one NodeResult
  naming that host and containing exactly its router SourceResult. This
  identity envelope is necessary even when no records exist. Preserve complete,
  partial and unavailable source states; canonical bytes carry
  anvil-workloads/v1, collection time and truncation without generic context.
- Unexpected clock/registry/serialization errors return fixed HTTP503
  workload_source_unavailable; never return a raw exception. Ordinary injected
  operator callback errors keep their existing behavior. T012 must validate
  the received one-router-source NodeResult against its expected local node,
  then retain the router SourceResult; an empty response never bypasses host
  verification.
- Add actual build_server HTTP tests for configured host identity, empty and
  populated canonical bytes, shared registry, disabled observation/missing
  host, filtering/limits/truncation, malformed/repeated query fields and
  seeded private data. Authorization tests prove missing/legacy/media-only/
  wrong-scope clients cannot touch registry or DecisionLog. Existing ordinary
  operator route, auth and config tests stay green. Final T016 documents the
  new optional server field and endpoint; no live configuration is changed.

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

Apply the local-producer record validation and honest anonymous-omission rules above. Use `validate_source_records` on each one-record candidate before inserting it into the deduplication map; retain fixed FUTURE/INVALID failure classification without exposing exception text. Do not relax the canonical source decoder. Use only phase counters for unrepresented and finalizing requests, not unbounded token/timestamp maps. A failed projection helper returns a fixed unavailable source, never raw internal exception text. The reproduced cases and rationale are recorded in `.tickets/2026-09-05-workload-projection-partiality.md`.

**Acceptance criteria:**

- Registry transitions enforce the fixed order and reject unknown/raw owner text; inactive tokens create no record.
- Cap 1024 saturation retains exact internal currently-unrepresented active counts and changes only observability metadata, never request success, admission, or dispatch. Freshness-sensitive queries report unknown omission for potentially matching anonymous requests; provable exclusions report zero. Saturated requests remain eligible for terminal projection.
- Concurrent/repeated finalization appends at most one existing `DecisionLog` record, always clears represented active state, and terminal projection reads at most 512 recent decisions while superseding same-ID active entries.
- Malformed or unavailable decisions/projection clocks fail safely without routing effects, ghost active entries, or a second terminal collection.
- Exactly 30 seconds of future skew is allowed; 30 seconds plus one microsecond is quarantined with fixed FUTURE error while a healthy active peer survives as partial. Future active-clock entries are handled identically. Unexpected projection-helper failures return a fixed unavailable result.
- A blocked saturated terminal sink cannot double-count one request as a returned terminal plus a numeric omitted active. Per-state finalizing counters clear on normal and failing sinks; stale anonymous requests never become false exact matching omissions.
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
**Dependencies:** T001, T003.1, T003.2

Add `BenchmarkJobStore.list_workloads(host, query, now) -> SourceResult` and the same method on `OperationStore`. Both classes live in `control_plane/controller/store.py`. Validate trusted host, canonical query and collection datetime first; provable owner/kind/host exclusions return complete empty without touching storage. Media follows in T010. The source finding is `.tickets/2026-09-05-store-workload-read-boundaries.md`.

T003 is the integration acceptance gate for the two independently reviewed implementation children below. Preserve this complete shared contract in each child packet; do not duplicate or independently reinterpret selection, serialization or timestamp semantics.

Use the existing owner RLock with a bounded acquire and a new SQLite `mode=ro`, query-only connection. Never call either existing `_connection()` helper: both create directories/tables and change journal mode. Never call `status`, `lookup`, `_record`, expiry or recovery. Begin one read transaction, issue one SELECT, copy primitive values, close before record construction, and leave all lifecycle writes unchanged. A single one-second monotonic deadline covers lock acquisition, SQLite busy timeout and execution; check it with a progress handler every 1000 VM instructions and again after fetch, clear the handler and close in finally. Missing database/table (including `operation_leases`), absent JSON support, busy/interruption or query failure returns fixed UNAVAILABLE with unknown omission, never creating a main database or schema. The deadline/clock seam is injectable for tests, not a new CLI option.

Read-only means no product lifecycle, schema, journal-mode or database-content writes. For an existing WAL database, SQLite itself may create/recreate its `-wal`/`-shm` coordination sidecars to join a coherent concurrent snapshot; zero filesystem side effects is not promised. Do not use `immutable=1`, `nolock=1`, exclusive locking, manual sidecar cleanup or a copied database to hide this behavior. Test a closed-writer WAL fixture: main database bytes/schema/rows stay unchanged and only SQLite-owned sidecars may appear; retain the concurrent-writer snapshot test and absent-main-database noncreation tests. This explicit clarification follows the [SQLite read-only WAL contract](https://www.sqlite.org/wal.html#read_only_databases) and [mutable-file warning for immutable mode](https://www.sqlite.org/uri.html).

Use fixed parameterized SQL CTEs to extract bounded metadata, normalize it, apply canonical state/freshness/recent predicates and order before `LIMIT min(query.limit, 200)+1`. Valid matching rows sort before malformed potential matches, then by canonical updated timestamp descending and workload digest ascending. This is lifecycle updated time, not lease heartbeat time. Unknown textual owner states map to unsupported without retaining raw text; non-text state remains invalid. An explicit state mismatch may exclude a row; otherwise malformed potential matches remain bounded sentinels after healthy rows. Process at most cap+1 candidates, validate every constructed candidate with `validate_source_records`, keep healthy survivors and fixed INVALID/FUTURE partial errors. An extra candidate means omitted=null, never an unbounded count or an invented exact remainder; without an extra or rejected row omission is zero. Use canonical selection for the bounded returned candidates with the effective source cap.

SQL window semantics must match `select_records`: active-only includes only active states with source age at most 30 seconds; default includes fresh active states, current configured/absent/unavailable/unsupported states regardless age, and terminal rows updated within the recent window. Exactly 30 seconds of age or future skew is allowed; one microsecond beyond is not. Validate future timestamps and created/updated/source ordering before admitting a row as valid. A bad row never makes the final SourceResult reject a healthy peer.

Register small deterministic scalar functions only on this read connection for canonical UTC microsecond-Z timestamps and `workload_id`. Benchmark extraction must guard TEXT storage, at most 8 MiB of JSON bytes, JSON validity and exact text timestamp fields before passing at most 65 characters to a scalar function. Do not select the full record or send it into Python, even on an error fallback. Extract only owner row identity, fixed mapped state and submitted/updated timestamps. Operations select only row identity, fixed mapped status and numeric created/updated/lease times; never select key, request ID, fingerprint, response, result or error. Reject NULL, text/blob, nonfinite or out-of-range epochs; stored REAL zero/one are epochs because SQLite has already erased any original boolean provenance.

For benchmarks use submitted time as created, and updated time as both updated and source observation. For running operations use the validated maximum of record update and matching lease heartbeat as source observation while preserving lifecycle updated time; terminal operations ignore lease time. A missing matching lease in an existing table is valid and allows old running work to become stale; a missing lease table is unavailable. Add `map_store_state(owner, state)` in the canonical schema module for controller/benchmark owners only, preserving the fixed mapping table and mapping unknown text (including benchmark cancelling) to unsupported. Wrong types fail with fixed invalid data.

Native workload identity is `benchmark-row:<rowid>` or `operation-row:<rowid>:<canonical-created-at>`, passed only to `workload_id`, never serialized. Caller run IDs, request IDs and idempotency keys are not owner-generated and must not enter identity construction. These digests identify current store rows through ordinary updates/restarts, not durable benchmark evidence. Operation creation time fences normal rowid reuse after expiry; neither identity promises continuity across VACUUM, rebuild or restore/import. No schema migration or new persistent identity is introduced in this slice.

**Acceptance criteria:**

- Controller operation and benchmark stores return deterministic active/recent bounded projections from one consistent snapshot.
- Unknown future owner states remain visible as unsupported rather than being mislabeled.
- Readers never observe partially updated rows during concurrent state transitions.
- Existing controller and benchmark lifecycle tests remain unchanged.
- Missing storage remains absent; traces and failing lookup/expiry/recovery spies prove no writes. Missing lease table is unavailable, while a missing lease row is a valid stale observation.
- More than 200 shuffled, unrelated or stale rows cannot hide the newest matching row or a fresh lease-backed operation. Equal timestamps use canonical digest order; the extra matching candidate reports unknown omission.
- Malformed and future metadata quarantine only affected rows; exact age/recent/skew boundaries and fresh versus absent lease timestamps are covered.
- Bounded SQL-function spies see no full JSON, caller IDs, specs, logs, responses or errors. Oversized JSON and invalid numeric epochs fail safely with no seeded private values in output or errors.
- A contended owner lock, forced SQL progress interruption and busy/query failure return fixed unavailable within the shared deadline; resource cleanup and lifecycle state remain unchanged.
- Removing pre-limit ordering or moving state/window filtering after the limit makes the beyond-first-200 regression fail. Removing per-row source validation makes the malformed/future-peer regression fail.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/store.py anvil_serving/observability/workloads.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py`

### T003.1: Implement bounded benchmark workload snapshots

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/store.py, anvil_serving/observability/workloads.py, tests/test_benchmark_jobs.py
**Dependencies:** T001

Implement the shared fixed state mapping, bounded read-only snapshot/deadline and scalar metadata helpers, then BenchmarkJobStore.list_workloads exactly as the parent T003 contract specifies. Keep helpers private in the owning store module; do not make a generic public SQL query API. Leave OperationStore lifecycle and projection unchanged until T003.2. Copy the existing injected-clock and temporary SQLite fixture idioms.

**Acceptance criteria:**

- Benchmark source obeys all parent bounds, identity, schema, state, timestamp, filter/order and partiality rules without payload materialization or database creation/writes.
- Tests prove newest matching rows beyond 200 insertion-ordered rows, equal-time digest order, extra-row unknown omission, future/invalid peers, unsupported states, oversized JSON and absent/busy/interrupted sources.
- Spies prove bounded scalar inputs, no caller IDs or full records, no status/expiry/recovery calls, coherent concurrent snapshots and bounded lock/query cleanup.
- Removing pre-limit selection/order or per-row validation makes the corresponding parent-mandated regression fail.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_jobs.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/store.py anvil_serving/observability/workloads.py tests/test_benchmark_jobs.py`

### T003.2: Implement lease-aware operation workload snapshots

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/store.py, tests/control_plane/test_benchmark_jobs.py
**Dependencies:** T003.1

Add OperationStore.list_workloads using the accepted shared snapshot and timestamp machinery. Apply parent T003 exactly: only safe row/lease primitives, creation-fenced owner identity, fresh running lease observation without changing lifecycle update order, strict numeric epoch interpretation and fixed source failures. No lifecycle write path or database schema changes. Do not fork benchmark selection or the canonical serializer.

**Acceptance criteria:**

- Running/terminal/unsupported states, rowid reuse fencing, canonical filter/order and partiality meet the parent contract; no caller key, request ID, fingerprint or result/error reaches projection helpers.
- Fresh versus absent lease rows, missing lease table, exact freshness/recent boundaries and numeric zero/one/nonfinite/text/out-of-range epochs behave as specified.
- Concurrent transitions remain atomic; stale rows cannot hide a newer or fresh lease-backed match beyond the first 200 rows.
- Read failures and deadline/lock contention create no database, expiry or recovery mutations; future/invalid peers survive with fixed partial errors.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_benchmark_jobs.py tests/test_benchmark_jobs.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/store.py tests/control_plane/test_benchmark_jobs.py`

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

### T004.1: Produce bounded recipe workload observations

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serve_recipes.py, tests/test_recipe_container_discovery.py, tests/test_serve_recipes.py
**Dependencies:** T001

Implement only the bounded recipe workload producer from the closed recipe observation contract. Preserve existing recipe lifecycle and inventory APIs; add the immutable two-component snapshot, bounded registry read and fixed metadata-only managed-container capture. Do not construct canonical workload records, add a health/model probe, mutate lifecycle, or expose raw capture data.

**Acceptance criteria:**

- Registry reads enforce the 8-MiB pre-materialization ceiling and stable open-handle metadata while runtime reads use only the fixed metadata template, 256-KiB capture ceiling, ten-second deadline and maximum 256 managed IDs.
- Configuration and runtime components retain distinct canonical status/error/omission metadata, timestamp successful reads immediately, preserve trustworthy peers after malformed/overflow/future input, and never retain raw exceptions or rejected values.
- Exact boolean/state and RFC3339Nano-to-UTC-microsecond rules preserve configured, observed-running, absent and unsupported evidence without producing health-plus-identity.
- Legacy load_registry and discover_recipe_containers behavior and existing recipe lifecycle callers remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_recipe_container_discovery.py tests/test_serve_recipes.py -x -q`
- `python -m ruff check anvil_serving/serve_recipes.py tests/test_recipe_container_discovery.py tests/test_serve_recipes.py`

### T004: Project recipe-managed serve workloads

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serve_recipes.py, anvil_serving/observability/workloads.py, tests/test_serve_recipes.py, tests/observability/test_workloads.py
**Dependencies:** T001, T004.1

Build the read-only recipe-managed adapter only from the T004.1 `RecipeWorkloadSnapshot`, retaining separate configuration, observation and freshness provenance. Use canonical `workload_id`, per-record validation and `select_managed_records`; do not invoke Docker, read the registry, mutate lifecycle or infer health-plus-identity. Manifest-managed serves follow in T011.

**Acceptance criteria:**

- Recipe workload IDs use only the exact recipe-config semantic digest or validated full container ID native namespace and are stable and safe without exposing either native value, commands, mounts, URLs, host paths or recipe paths.
- Exact digest reconciliation suppresses only the matching configured row, retains multiple real containers, and projects configured, observed-running, stale, absent, unsupported and component inspection-error evidence distinctly without ever emitting healthy-identity.
- Canonical filtering and newest-updated/ID ordering occur before the 200-record source cap; complete input reports exact omissions while producer partiality reports unknown omissions and preserves trustworthy peers.
- Projection performs no lifecycle mutation, registry read, Docker call, raw-status fallback, health probe or identity probe.

**Verification:**

- `python scripts/run_tests.py tests/test_serve_recipes.py tests/observability/test_workloads.py -x -q`

### T004.2: Correct recipe workload validation and reconciliation

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/serve_recipes.py, tests/test_recipe_workload_projection.py
**Dependencies:** T004, T004.1

Repair the projection defects recorded in the recipe workload reconciliation
ticket without changing producers or lifecycle. First reproduce query
truncation and invalid/future runtime suppression using literal immutable
snapshots. Validate the exact query, safe host and collection time before
calling the reader. Revalidate frozen query fields; malformed calls fail
through the canonical typed validation boundary before any source read.

Validate the snapshot envelope once and each configuration/runtime component
independently. Exact component types, enum status, tuple of at most 256 rows,
bounded omission (0..1000000000 or null), allowed fixed errors, UTC observation
time and complete/partial/unavailable consistency follow the existing closed
contract. A malformed component becomes fixed INVALID unavailable without
discarding its valid peer; bad individual rows are quarantined independently.
Complete/partial components require a source observation time; failed empty
components may omit it. Validate source and lifecycle timestamps against both
their component observation and collection time using the canonical validator.

Only exact typed observations with lowercase 64-hex digests and full container
IDs enter identity construction. Runtime recipe digest may be null; runtime
state is exactly running, absent or unsupported. Validate and deduplicate
runtime candidates before adding their digests to reconciliation. A rejected
or duplicate runtime row cannot suppress a configured row. Keep the first
valid row for each native ID, all distinct real containers and valid configured
peers; duplicate input is INVALID partial. Unknown digest suppresses nothing.
Use existing recipe namespaces and canonical managed selection, with no new
I/O, probe, shared state or generic framework.

Normal query truncation of complete input returns PARTIAL, exact omitted
count and no source error. Producer or validation partiality instead reports
unknown omission and fixed INVALID, FUTURE, UNAVAILABLE precedence. Both failed
components are unavailable; a successful empty peer remains partial, not false
all-source failure. Validate final selected records before constructing the
SourceResult. Follow the manifest projector's independently validated-candidate
idiom while keeping recipe data types and namespaces distinct.

**Acceptance criteria:**

- Literal regressions fail on the predecessor's truncated COMPLETE result and pre-validation suppression, then pass after the correction.
- Invalid/future/duplicate runtime rows cannot hide valid configured peers; malformed components and rows preserve independently valid data.
- Query filters precede the 200-row cap; exact normal omissions and unknown source omissions remain distinct.
- IDs, times, component consistency and fixed error precedence match the closed contract without raw private values or extra I/O.

**Verification:**

- `python scripts/run_tests.py tests/test_recipe_workload_projection.py tests/test_serve_recipes.py tests/test_recipe_container_discovery.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/serve_recipes.py tests/test_recipe_workload_projection.py`

### T011.1: Produce bounded manifest workload observations

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/manifest_workloads.py, anvil_serving/controller_diagnostics.py, tests/test_manifest_workloads.py, tests/test_controller_diagnostics.py
**Dependencies:** T001

Implement the closed observation-only manifest producer above and the opt-in completed-error stdout retention in the existing bounded child capture. Do not call lifecycle manifest loading/status or add another subprocess runner. Return only immutable bounded observation values; canonical workload projection follows in T011.

**Acceptance criteria:**

- Bounded deterministic discovery/read/capture keeps configuration and runtime evidence distinct and preserves valid peers without inspecting undeclared containers.
- Configured identity is observation-only; unsupported native or Docker syntax never becomes launch validity, running, healthy identity or subprocess authority.
- Exact Compose/name/ID ownership, timestamps, mirror/conflict rules and partiality match the closed contract with no raw private data retained.
- Opt-in capture preserves only completed nonzero stdout with unavailable status; legacy callers and all timeout/overflow/read/cleanup failure behavior remain unchanged.
- Tests cover every closed bound, grammar, identity and failure boundary without live commands or lifecycle mutation.

**Verification:**

- `python scripts/run_tests.py tests/test_manifest_workloads.py tests/test_controller_diagnostics.py -x -q`
- `python -m ruff check anvil_serving/manifest_workloads.py anvil_serving/controller_diagnostics.py tests/test_manifest_workloads.py tests/test_controller_diagnostics.py`

### T011: Project manifest-managed serve workloads

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/manifest_workloads.py, tests/test_manifest_workloads.py, tests/observability/test_workloads.py
**Dependencies:** T001, T011.1

Project only the immutable manifest snapshot through canonical workload_id, independent candidate validation and select_managed_records. Reconcile exact config digests and follow the closed manifest source contract. Do not read manifests, call Docker/status, mutate lifecycle, or infer health/model identity in the projection.

**Acceptance criteria:**

- Manifest and recipe records share kind `recipe-serve` but have distinct stable owner IDs.
- Configured-only, running-observed, stale, absent, unsupported and component inspection-error cases map exactly with bounded canonical filtering and honest omissions.
- Neither health nor configuration can produce `healthy-identity`; v1 never emits it.
- Container IDs, commands, mounts, URLs, paths, engine/quantization selection, and raw inspection errors are absent.

**Verification:**

- `python scripts/run_tests.py tests/test_manifest_workloads.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/manifest_workloads.py tests/test_manifest_workloads.py tests/observability/test_workloads.py`

### T011.2: Preserve valid manifest runtime peers after failed capture

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/manifest_workloads.py, tests/test_manifest_workloads.py
**Dependencies:** T011.1, T011

Correct only failed-runtime component construction in
capture_manifest_workload_snapshot. Retained unsupported native/generic
observations make a failed Compose inspection PARTIAL, with fixed UNAVAILABLE
error and omitted null; UNAVAILABLE is reserved for zero trustworthy runtime
rows. Preserve each retained row's original observation/lifecycle timestamps.
When the failed component has no capture observation time but retains rows,
use the existing configuration observation time, never the collection clock.
Keep capture bounds, partial successful parsing and all lifecycle code intact.

Add hermetic mixed native/Compose and generic/Compose fixtures for nonzero empty
capture, thrown capture failure, malformed/truncated capture, and empty or
malformed successful output. Pass each resulting snapshot through the canonical
manifest projection and prove retained unsupported peers, configured Compose
fallback, fixed errors and unknown omissions. Add no-peer failure controls and
a no-subprocess native-only control. Record the literal predecessor failure
before modifying production.

**Acceptance criteria:**

- A failed component with trustworthy rows is PARTIAL and never an invalid UNAVAILABLE-with-records combination.
- Canonical projection retains valid unsupported peers and never upgrades them to running, healthy identity or idle.
- Empty failed runtime remains UNAVAILABLE and valid configured peers remain available.
- All tests are hermetic; source bounds, timestamps and default capture behavior remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_manifest_workloads.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/manifest_workloads.py tests/test_manifest_workloads.py`

### T005.1: Declare the router workload host identity at startup

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/config.py, tests/router/test_config.py
**Dependencies:** T009

Implement only the optional ServerConfig.workload_host parser field from the
closed endpoint contract. This is operator-supplied canonical host identity,
never an inference from a URL, environment, OS or request. Leave endpoint and
server forwarding to T005.

**Acceptance criteria:**

- Absent workload_host is None and existing server fields retain their behavior.
- Exact canonical IDs parse unchanged; wrong types, empty/oversized values, whitespace, non-ASCII, punctuation and injected paths/URLs fail with fixed ConfigError prose that never echoes input.
- Tests call load_server_config on actual temporary TOML; no identity discovery or environment lookup is performed.

**Verification:**

- `python scripts/run_tests.py tests/router/test_config.py tests/router/test_front_door_auth.py -x -q`
- `python -m ruff check anvil_serving/router/config.py tests/router/test_config.py`

### T005: Add the authenticated router workload endpoint

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/front_door.py, tests/router/test_operational_endpoints.py, tests/router/test_front_door_auth.py
**Dependencies:** T009, T005.1

**External prerequisites:** fleet-node-enrollment:T008, fleet-node-enrollment:T009, and fleet-node-enrollment:T010 must each be `done` before this task is claimed. Anvil's PRD parser supports local dependency IDs only; the coordinator must run `anvil show fleet-node-enrollment:T008 --prd fleet-node-enrollment --json`, `anvil show fleet-node-enrollment:T009 --prd fleet-node-enrollment --json`, and `anvil show fleet-node-enrollment:T010 --prd fleet-node-enrollment --json`, require `.data.task.status == "done"` for every result, and retain all results in the dispatch packet. A missing task is an unmet prerequisite, not permission to proceed.

Implement the Router workload endpoint ownership and wire contract above. Expose authenticated GET /v1/workloads using the canonical query/NodeResult serializer, the registry already created by build_server and the dedicated workloads:read gate. Forward the trusted server workload_host parsed by T005.1 at startup; never infer it or accept a caller override. This slice is router-local only; node aggregation and fleet fan-out follow.

**Acceptance criteria:**

- Data-plane, legacy, media-only, missing-policy, and wrong-scope credentials are denied before registry/DecisionLog reads.
- Unknown/repeated scalar filters, out-of-range windows/limits, malformed times, and unsupported kinds/states fail with fixed safe errors.
- Valid queries return exact `anvil-workloads/v1` canonical bytes and explicit truncation metadata.
- The canonical one-source node envelope identifies the configured host even with zero records; missing host/registry refuses after authorization without source reads.
- Actual build_server tests prove parser-to-handler host/registry wiring and fixed query/source failures without changing ordinary config/auth behavior.
- Endpoint success and errors contain no request content, route endpoint, token, raw response, or exception.

**Verification:**

- `python scripts/run_tests.py tests/router/test_operational_endpoints.py tests/router/test_front_door_auth.py tests/router/test_workloads.py tests/router/test_config.py tests/router/test_serve_cli.py -x -q`
- `python -m ruff check anvil_serving/router/config.py anvil_serving/router/serve.py anvil_serving/router/front_door.py tests/router/test_operational_endpoints.py tests/router/test_front_door_auth.py`

### T012.1: Read benchmark workloads without constructing a writable store

**Feature:** F003
**Priority:** high
**Type:** refactor
**Likely files:** anvil_serving/control_plane/controller/store.py, tests/control_plane/test_benchmark_jobs.py
**Dependencies:** T003

BenchmarkJobStore.__init__ creates its run root. Node visibility must not call
that constructor merely to read an existing database. Add the owner-module
function read_benchmark_workloads(path, host, query, now, *,
_snapshot_clock=time.monotonic, _lock=None). Factor the existing bounded
list_workloads projection into this one implementation; the instance method
delegates with its exact path, snapshot clock, and owner lock. With no injected
lock the function uses a fresh in-memory RLock. Do not instantiate a store,
bypass __init__ with __new__, accept a run root, hydrate jobs, or add new SQL
projections outside the owning module.

Validate query, trusted host, and collection time before filesystem or SQLite
access. Resolve a non-empty string or PathLike database path only on the read
path; invalid/missing/unreadable source paths produce the existing fixed
UNAVAILABLE source, never create parents, a database, or a run directory.
Retain mode=ro, query-only transactions, the shared one-second lock/SQLite/scan
deadline, metadata-only projection, filtering, truncation, and fixed errors.
The current constructor and all writable lifecycle APIs keep their behavior.
Normal SQLite read coordination is not a workload lifecycle action; do not use
immutable=1, which would discard live WAL visibility.

**Acceptance criteria:**

- Module-level and instance readers produce literal canonical-equivalent output for the same real existing database and clock, including fresh WAL commits, filters, empty results, corrupt/future rows, and truncation.
- A missing database under missing parents returns UNAVAILABLE and leaves the directory tree unchanged; no run-root, initialization, recovery, writable connection, job hydration, or writable store constructor is called.
- Invalid query/host/time fails before path access, lock acquisition, or reads. Excluded owner/kind/host queries retain the existing no-read COMPLETE result.
- Instance delegation preserves the actual owner lock and snapshot clock; lock contention still terminates within the existing bounded deadline.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_benchmark_jobs.py tests/test_benchmark_jobs.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/store.py tests/control_plane/test_benchmark_jobs.py`

### T012.2: Read media workloads without initializing a database

**Feature:** F003
**Priority:** high
**Type:** refactor
**Likely files:** anvil_serving/media/jobs.py, tests/media/test_jobs.py
**Dependencies:** T010

MediaJobStore.__init__ initializes schema and WAL state. Add the owner-module
function read_media_workloads(path, host, query, now, *,
_monotonic=time.monotonic, _lock=None), factoring the existing list_workloads
projection into one implementation. Its instance method delegates with the
exact path, owner lock, and supplied monotonic clock; the standalone function
uses a fresh in-memory RLock when no lock is supplied. Keep writable store
construction and lifecycle behavior unchanged. Do not construct a store,
bypass initialization via __new__, add a second SQL owner, or call a lifecycle
factory merely to observe an existing source.

Validate the canonical query, trusted host, and collection time before path
or database access. Resolve only a non-empty string or PathLike path on the
read path. Invalid/missing/unreadable paths yield the existing fixed
UNAVAILABLE source without creating directories or a database. Retain the
read-only/query-only SQLite transaction, bounded streaming heap, one-second
lock/SQLite/scan deadline, current filtering and invalid-row isolation.
Do not use immutable=1: live WAL commits must remain visible.

**Acceptance criteria:**

- Standalone and instance reads are canonical-equivalent against one real database, including live WAL commits, empty/filter/truncation cases and corrupt/future records.
- Missing parents/database remain absent after collection; no schema initialization, writable connection, lifecycle construction, job/event/ artifact hydration, recovery, or mutation method is called.
- Invalid query/host/time fails before path/lock/SQLite access. Existing excluded-source no-read behavior remains unchanged.
- The instance forwards its actual owner lock and monotonic clock, retaining the existing bounded contention behavior and fixed safe errors.

**Verification:**

- `python scripts/run_tests.py tests/media/test_jobs.py -x -q`
- `python -m ruff check anvil_serving/media/jobs.py tests/media/test_jobs.py`

### T012: Aggregate node-local workload sources

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/controller/server.py, anvil_serving/observability/probes/remote_controller.py, tests/test_controller.py, tests/observability/test_remote_controller.py
**Dependencies:** T003, T004, T004.2, T005, T010, T011, T011.2, T012.1, T012.2

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
