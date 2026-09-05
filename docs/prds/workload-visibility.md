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

- Structural PRD approval authorizes implementation, not final acceptance. Record the actual agent reviewer explicitly during autonomous execution; retain candidate evidence in needs_review until the consolidated batch review and acceptance. Never describe a default reviewer label as proof of a human decision.
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
- Canonical record bytes are shared across surfaces. Application envelopes add node/source status, collection time, completeness, and truncation; never pass generic command/transport context through them. The single protocol-only exception is a validated JSON-RPC correlation ID echoed once in its outer wrapper as closed in T012, never copied into application content, metadata, audit or storage. Whole-application-envelope adversarial tests seed credentials, user paths, tool payloads, URLs, private addresses and raw exceptions.
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

### T012.3: Compose bounded canonical node results

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/workload_collection.py, tests/observability/test_workload_collection.py
**Dependencies:** T001

Add the pure owner-independent function build_node_workloads(host, query, now,
sources) in the new sibling module workload_collection.py. It consumes
already-collected SourceResult values; it never invokes a reader, constructs a
store, loads configuration, reads a clock, submits work, or performs I/O.
sources must be an exact dict of at most six exact WorkloadOwner keys to
SourceResult or None. Reject a malformed outer mapping with fixed WorkloadError
before traversing values. Validate canonical host/query/time first, including
an exact WorkloadQuery and canonical timestamp/host validators. Always return
the six owners in lexicographic owner.value order; missing/None owners become
fixed UNAVAILABLE sources collected at now, with no records and omitted=None.

Each present source is a separate failure boundary. Require its exact
SourceResult type, matching owner, exact tuple of at most200 exact
WorkloadRecord values and exact Truncation. Reconstruct records (and optional
exact Progress) through canonical constructors before serialization,
then reconstruct SourceResult with a fresh tuple and truncation. Do not
serialize an unbounded or arbitrary object first. Use existing validators,
not an alternate record schema or recursive asdict. A malformed source becomes
fixed invalid-workload for only that owner; too-far-future source/record time
becomes future-workload-timestamp. Preserve valid peer sources.

Use validate_source_records with the expected host and the source's original
collection timestamp, then ensure its collection time is not over30 seconds
ahead of node now. Preserve original source and lifecycle timestamps. The
collector calls each source with this same query; verify returned records
equal select_records(records, query, now=now)'s ordered result, with no
discarded record. A source violating query/filter/order/limit is invalid,
rather than allowing a remote producer to bypass the requested view. An empty
source still has its owner and collection-time checks. Freshness remains
source-derived; do not restamp old observations as fresh.

The six individually valid sources can total1200 records, so do not pass
that unbounded-for-the-API tuple into select_records(aggregate=True), whose
input contract caps at1000. Flatten at most1200 validated records with their
source/index identity; use two stable sorts (ID ascending, then updated_at
descending), retaining the first min(query.limit,1000). Equal timestamp/ID
ties retain canonical owner order. Redistribute selected entries to their
original sources in the same order, without deduplicating by digest across
owners or erasing empty source summaries. This is a bounded merge of already
filtered records, not a second filtering policy.

For each source, add only global-cap removals to a known omitted count;
unknown omissions stay None and count overflow past MAX_COUNT becomes None.
Do not call filtered-out records omissions. Preserve existing error codes.
A COMPLETE source reduced by the aggregate cap becomes PARTIAL with no
invented error; existing PARTIAL/UNAVAILABLE states remain honest. Recompute
node status by the canonical all-unavailable/any-non-complete/otherwise-complete
rule and construct NodeResult. Original input objects remain unchanged.
No source payload, path, URL, credential, raw exception, callable, or generic
transport context may be returned.

**Acceptance criteria:**

- Literal canonical node fixtures cover all-complete, complete-empty, one surviving source, all-unavailable, explicit PARTIAL errors, absent owners, and stable six-owner order.
- Wrong owner/host, forged malformed records, oversized tuple and arbitrary/subclass source values fail only their source without traversal or leaking seeded private values; bad outer mappings and invalid query/host/time fail before processing sources.
- Source time exactly30 seconds ahead remains allowed; greater skew fails that source; old source timestamps and lifecycle times are preserved and never refreshed by aggregation.
- A six-by-200 fixture is reduced to the exact newest1000 (or lower query limit) with literal boundary IDs and honest per-source omissions/status; known and unknown omissions, overflow, ties and unchanged inputs are covered.
- Source records that violate the requested filter, stable ordering or limit are quarantined; canonical round-trip succeeds and no clock, store, filesystem, subprocess, network, or reader callback is invoked.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workload_collection.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/workload_collection.py tests/observability/test_workload_collection.py`

### T012.3.1: Preserve canonical unknown progress totals

**Feature:** F003
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/workload_collection.py, tests/observability/test_workload_collection.py
**Dependencies:** T012.3

Correct only _validated_progress so canonical Progress.total=None survives
reconstruction. Progress.completed remains an exact int; a present total
remains an exact int, never bool or an int subclass. Continue reconstruction
through Progress to enforce bounds, completed <= total and the fixed unit
vocabulary. Do not change the schema, source state, sorting, limits or I/O.
The reproduced predecessor behavior is recorded in the node workload
composition boundaries ticket.

**Acceptance criteria:**

- Unknown, known and zero totals survive composition and canonical JSON round-trip without changing source status, record bytes or input objects.
- Forged boolean, subclass, negative, oversized, completed-over-total and private-text totals still fail only their source with fixed INVALID and preserve healthy peers.
- The unknown-total regression fails against the predecessor before the one-guard correction.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workload_collection.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/workload_collection.py tests/observability/test_workload_collection.py`

### T012.4: Bound concurrent node-source collection

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/node_workload_collector.py, tests/observability/test_node_workload_collector.py
**Dependencies:** T012.3

Add NodeWorkloadCollector(host, readers, *, monotonic=time.monotonic) in a new
sibling module. readers is an exact dict of at most six exact WorkloadOwner
keys to a trusted callable or None; each callable receives (host, query, now).
Copy this fixed registration once and validate the canonical host. Malformed
configuration raises fixed ValueError without input echo. Construction reads
no sources, clock or files and starts no threads. This collector is a bounded
request coordinator, not a workload store, cache, discovery registry or plugin
framework.

collect(query, now) first obtains the canonical empty/unavailable fallback
through build_node_workloads(host, query, now, {}). That validates exact
query/host/time before any source callback or scheduling. Only one collection
may be active per collector; a concurrent call returns its canonical
unavailable fallback immediately rather than queuing or waiting. Missing
readers remain explicit unavailable sources. The active call uses a fixed
1.5-second monotonic collection deadline, leaving room within the fleet's
two-second per-node request budget.

Use at most six lazy persistent daemon workers for the collector lifetime,
one for each registered non-None owner. Each owner has at most one queued or
running job; no executor with an unbounded queue, per-request threads, retries,
or replacement workers for stalled callbacks. A later collection can use idle
owners while an older timed-out callback still occupies another owner. Busy
owners are unavailable for the new collection, never awaited or presented as
idle. Source calls and all monotonic clock reads occur outside the coordinating
condition/lock. Do not hold any router/admission/store lock in this layer.

Jobs carry collection identity, copied canonical query/time and their deadline.
Wait only on the condition with the remaining deadline; no sleeps, polling
loops or busy waiting. Validate clock samples as finite nonnegative built-in
numbers (not bool); invalid or backwards readings abandon this collection to
its unavailable fallback. Callback exceptions become fixed unavailable sources,
without logging or retaining exception text. A callback that finishes past its
collection deadline is discarded. A timely returned value goes through
build_node_workloads so malformed values fail only their owner.

On return or exception, clear unclaimed jobs and abandon outstanding result
slots for this collection. Late running completions must not populate a future
query or become cached observations. Running jobs keep their one-owner slot
until they actually finish; healthy idle owners remain usable. Maintain only
bounded in-flight request metadata and detached canonical results; do not keep
a result history. close() is idempotent and nonblocking: mark closed, clear
unclaimed jobs, wake idle workers and abandon results. Work already claimed by
a worker may finish but cannot publish after close. Future collect calls return
canonical unavailable without scheduling. No unbounded join or unsafe thread
cancellation.

The server must eventually own one collector and close it with server lifecycle;
that wiring and real source/configuration binding are explicitly later T012
work. This task only implements and tests the coordinator with injected readers,
not HTTP, topology, credential loading or any live managed observation.

**Acceptance criteria:**

- Event-controlled readers prove one healthy source survives blocked peers and collection returns within the fixed deadline; concurrent collect calls do not queue or start duplicate source work.
- Repeated timed-out queries with all readers blocked create at most six persistent workers and at most one job per owner; a subsequent query can collect a healthy idle owner while another remains occupied.
- Invalid arguments and invalid/backwards clocks fail before new scheduling where detectable; source/clock callbacks never execute while the coordinating lock is held.
- A late completion cannot contaminate another query, return cached work, or revive a closed collector; queued-but-unclaimed callbacks are discarded on timeout/close.
- Callback failures/malformed source values are isolated, no raw errors or seeded private data escape, complete/partial/unavailable output uses the canonical builder, and all test workers are released and joined in bounded cleanup.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_node_workload_collector.py tests/observability/test_workload_collection.py -x -q`
- `python -m ruff check anvil_serving/observability/node_workload_collector.py tests/observability/test_node_workload_collector.py`

### T012.5: Read the authenticated declared router workload source

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/probes/router_workloads.py, tests/observability/test_router_workload_source.py
**Dependencies:** T005, T012.3, T012.3.1

Add read_router_workloads(endpoint, auth_env, host, query, now, *,
environment=None, _open=None) -> SourceResult in a new bounded source module.
This function consumes an explicitly selected local resource endpoint; it
does not read topology, discover ports, construct a router registry or access
router storage. Validate exact canonical host/query/time first via the node
builder's empty fallback, then copy the canonical query fields. Invalid
query/host/time raises the existing fixed WorkloadError before configuration,
environment or network access.

Accept only an exact ASCII endpoint string of at most2048 characters, with
exact http scheme, hostname127.0.0.1, an explicit decimal port1..65535, path
/v1 or /v1/, and no userinfo/query/fragment/controls/whitespace/percent escapes.
Reject alternate numeric spellings, implicit ports, localhost, DNS, remote
addresses and trailing-dot aliases. auth_env is an exact ASCII identifier
[A-Za-z_][A-Za-z0-9_]{0,255}. Bad/missing endpoint, reference, credential or
runtime alias returns fixed UNAVAILABLE without opening a socket.

environment=None means os.environ only; an injected Mapping is hermetic and
never falls back to ambient values, dotenv, config home or a credential file.
Read only the explicitly named outbound value and the existing loopback-alias
control when needed. Normalize the credential using the existing scoped
authorization._normalize_credential helper, then require ASCII material for
the HTTP Bearer header; keep the existing16..4096-byte bound. Never reuse
an inbound token or inspect authorization-policy internals. Do not retain
the value in a binding dataclass, result, repr, log or exception.

After validating the declared loopback endpoint, apply paths.runtime_url
with the explicit environment to honor the existing container-to-host alias.
Append /workloads to the normalized /v1 base. Encode exactly the seven
canonical query fields with urllib.parse.urlencode, enum.value and lowercase
true/false; omit absent optional filters and include the scalar defaults.
There is no arbitrary caller query, request ID, timestamp, context, body,
alternate method, retry or secondary endpoint.

Issue one GET with Accept application/json and the declared Bearer header,
using transports._urlopen_no_proxy_no_redirect by default and a fixed
one-second socket timeout. Read at most MAX_JSON_BYTES+1 bytes, reject
non-bytes/overflow, require HTTP200, and close the response. Redirects and
HTTP failures are never followed or read for explanatory text. Close an
HTTPError without materializing its body. The caller-owned persistent
NodeWorkloadCollector supplies the1.5-second wall-clock deadline and one
in-flight reader bound even if an injected or trickling response outlasts a
socket timeout; do not claim the socket timeout alone is a total deadline.

Decode only through node_result_from_json. Require the expected node even
with zero records and exactly one source whose owner is ROUTER. Node and
source collection time must each be no more than30 seconds ahead of now.
Pass the extracted source through build_node_workloads(host,query,now,...)
and return only its router source, so host, timestamp, query/order/limit,
forged-object and detached canonical rules remain shared. Preserve complete,
partial, unavailable, fixed source errors, original times and truncation.
Bad schema/shape/identity/query is fixed INVALID, excessive future time is
FUTURE, network/config/credential failure is UNAVAILABLE. No raw exception,
response, endpoint, reference, alias, address or credential escapes.

**Acceptance criteria:**

- Literal one-router-source wire fixtures retain canonical-equivalent populated, empty, partial and unavailable results, including original timestamps and omissions.
- Endpoint/credential/alias rejection happens before opener calls; an injected empty mapping cannot read ambient credentials or dotenv, and the actual request uses only the exact declared endpoint, GET, bounded query and two fixed headers.
- Wrong node even when empty, wrong/multiple owners, schema/extra fields/duplicates, oversized body, malformed records, filter/order violations and future boundaries fail with fixed typed source errors.
- Default networking is proxy-free and redirect-rejecting;200 success and response/HTTPError cleanup are tested without live hosts, while failure bodies and exception text are never returned or logged.
- Seeded private data is absent from every returned/repr/error/log value and independent healthy sources survive router failure through canonical node composition.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_router_workload_source.py tests/observability/test_workload_collection.py -x -q`
- `python -m ruff check anvil_serving/observability/probes/router_workloads.py tests/observability/test_router_workload_source.py`

### T012.6: Bind explicit node workload readers without discovery

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/control_plane/controller/workload_sources.py, tests/control_plane/test_workload_sources.py
**Dependencies:** T012.1, T012.2, T012.5

Add build_workload_readers(host, operation_store, *, benchmark_db=None,
media_db=None, recipe_registry=None, manifest=None, router_topology=None,
router_resource=None, router_auth_env=None, environment=None) in a new
controller sibling module. Return an exact six-owner dictionary of trusted
(host,query,now) callbacks or None for NodeWorkloadCollector. This is binding
only: no server, CLI, HTTP, scheduler, new persistent store or generic plugin
registry. Validate the configured exact canonical host before configuration.
The operation_store argument is the exact existing server-owned OperationStore
or None; reject another type with fixed ValueError. Never instantiate a store
or run recovery. Callback use of an injected/subclass owner is not supported.

Optional paths are explicit exact strings, nonempty, at most4096 characters,
without surrounding whitespace, controls or a leading tilde. Normalize them
to absolute paths once against the startup cwd using os.path.abspath; this
does not discover/open any file. Missing or malformed optional values disable
only that source. Never call config_home, default path resolvers, cwd search,
dotenv, init, exists/stat, store constructors or lifecycle factories to fill
missing values. A configuration/query cannot open a source that was absent
at construction. Return callbacks, not a printable dataclass containing
private paths, endpoints or environment references.

Bind CONTROLLER to this exact owner's list_workloads; BENCHMARK to the owner
module read_benchmark_workloads; MEDIA to read_media_workloads; RECIPE to
list_recipe_workloads; MANIFEST to list_manifest_workloads, each with only
the captured explicit path plus trusted host/query/time. No cross-module SQL,
payload hydration, raw subprocess/Docker call, lifecycle mutation, model
health/identity probe, or second source implementation. Existing managed
projection owns its bounded capture and source-observation clock.

Every returned callback first validates canonical query/time via the empty
node builder and requires its host argument to equal the configured host.
Provable owner/kind/host exclusions return a canonical COMPLETE empty source
without owner/file/environment/network access. Other filters go unchanged
to the authoritative reader; do not pre-limit or reinterpret rows. Missing
configuration remains None/unavailable, not a fabricated empty source.

Router registration requires all three explicit router_topology,
router_resource and router_auth_env values. Resource ID uses the existing
canonical safe ID grammar and the outbound environment name uses the exact
T012.5 grammar. Partial or invalid triples disable only ROUTER. Capture only
the declared outbound environment value and the existing loopback-alias
control; environment=None reads os.environ, while an injected Mapping is
hermetic. Do not copy the whole process environment, resolve dotenv or inspect
an inbound authorization policy. Missing/invalid credential stays unavailable
through T012.5. Registration performs no topology or source I/O.

The authenticated router callback loads only the captured topology path
through load_topology, selects topology.resource(exact_id), and requires
resource.role == router-workloads, endpoint_kind == workloads-v1,
resource.host == configured host, resource.workload == service, and a declared
runtime whose host matches. This uses existing generic Resource fields; add
no schema field or second role=router owner that would make legacy
resource_owner(router) ambiguous. A dedicated observation resource can
coexist with the ordinary data-plane router resource unchanged. T012.5
validates the exact loopback endpoint before any socket. Pass the explicit
auth reference and captured two-value environment to read_router_workloads.
Never choose a first matching resource, infer a port or hostname, or issue SSH.

Catch source-binding/configuration failures into a fixed UNAVAILABLE source
without error text; preserve a SourceResult returned by its authoritative
owner and let NodeWorkloadCollector's canonical builder validate/detach it.
Invalid canonical arguments still raise before reads. Source isolation means
a bad router topology cannot remove the controller/media peers. Expose only
build_workload_readers publicly. Server lifecycle ownership and startup flags
follow in T012; this slice performs no actual live collection.

**Acceptance criteria:**

- Construction opens no files, reads no stores, starts no workers and invokes no lifecycle/discovery helper; optional missing sources are None and the exact server OperationStore is retained without recovery.
- Each registered callback forwards only its exact path/host/query/time to the existing owner reader; explicit filters exclude unnecessary managed captures before I/O.
- Relative explicit paths bind once to startup cwd; missing/malformed/tilde paths never trigger ambient operator-home or cwd file discovery, including an injected empty environment.
- Router selection requires the exact dedicated resource and same host/runtime, retains ordinary router ownership, and passes only its explicit loopback endpoint/auth reference and two-value environment.
- Wrong host, invalid query/time, partial configuration, bad topology/resource/runtime and throwing owners return the specified fixed results without leaking paths, credentials, raw error or transport metadata.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_workload_sources.py tests/observability/test_router_workload_source.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/workload_sources.py tests/control_plane/test_workload_sources.py`

### T012: Aggregate node-local workload sources

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/workload_tools.py, anvil_serving/control_plane/controller/server.py, anvil_serving/control_plane/controller/http.py, tests/control_plane/test_controller_workloads.py
**Dependencies:** T003, T004, T004.2, T005, T010, T011, T011.2, T012.1, T012.2, T012.3, T012.3.1, T012.4, T012.5, T012.6

Wire one persistent NodeWorkloadCollector to the actual controller server and
expose the reserved normalized node_workloads tool on REST /tools/call and
controller /mcp. This is node-local only; fleet fan-out and standalone MCP
registration follow. No controller GET workload route or alternate transport
is introduced. ControllerTransport already posts /tools/call and requires
the boolean ok field, so its existing operation path remains sufficient.

Add keyword-only workload_benchmark_db, workload_media_db,
workload_recipe_registry, workload_manifest, workload_topology,
workload_router_resource and workload_router_auth_env (all defaultNone) to
make_server and serve; CLI forwarding follows in T012.7. Use only the existing
node_id as canonical workload identity. With a valid node_id, construct one
collector over build_workload_readers and the exact OperationStore already
owned/reconciled at startup. No new store or per-query collector. Missing or
invalid node_id disables collection and returns fixed source-unavailable
after authorization; it does not change legacy health/auth APIs. Add injectable
workload_clock (UTC now by default) and workload_monotonic seams for hermetic
tests, not CLI flags. No source read occurs during construction. Ensure
server_close closes this collector nonblockingly and idempotently before
delegating to the original server close; construction/bind failure also closes
it. Preserve the supplied server_class and IPv6/lifecycle behavior.

Add a small pure observability/workload_tools.py with the canonical
node_workloads declaration, fixed workloads:read metadata and an input schema
for exactly the seven canonical query fields. additionalProperties is false;
owner/kind/state enums, host grammar and max64 length, exact boolean
active_only, recent1..86400/default3600 and limit1..1000/default200 match
WorkloadQuery. This descriptor is reused by later MCP registration, not
hand-copied into parallel schemas. The handler receives only a parsed query;
query.host is a filter, never server identity. Parse invalid arguments before
reading the clock or invoking collection.

Application response is exactly success {ok:true,data:<node_result_to_dict>}
or failure {ok:false,error:{code:<fixed>,message:<fixed>}}. No request_id,
context, details, metadata, warnings or generic command/transport dictionary.
Tool-level outcomes use HTTP200, including invalid_workload_query,
idempotency_not_supported, invalid_workload_request and
workload_source_unavailable fixed code/message pairs. Actual authorization
and protocol failures keep their appropriate HTTP/JSON-RPC status. A valid
canonical unavailable NodeResult is successful data, not a transport failure.
Unexpected clock/collector/serialization exceptions become fixed source
unavailability. Never invoke generic call_tool_func, request-ID augmentation,
OperationStore.claim/executing/complete/lookup or operation context for this
tool.

Seal the normalized node_workloads name against injected catalog/callback
replacement. Add the canonical declaration before allowlist processing.
A supplied declaration may match it exactly once (supporting future canonical
MCP catalogs); a different or duplicate reserved declaration fails construction
with fixed ControllerError and no raw descriptor. Existing allowed_operations
continues to restrict visibility and dispatch. Removed/disallowed reserved
names still cannot fall through to legacy undeclared-tool dispatch or acquire
weaker scope. Authorized but disabled/disallowed invocation is a fixed failure,
never an injected callback. Ordinary unrelated tool behavior remains unchanged.

Recognize this tool before idempotency parsing and require workloads:read
before any source read. Reject presence of any X-Anvil-Idempotency-Key header,
even empty/repeated/malformed, without calling the generic key validator or
persisting anything. REST body requires exactly name and arguments; MCP params
requires name and arguments and may include only _meta additionally. Reject
context and unknown outer/parameter fields before collection. Arguments must
be an exact object; null is not silently an empty workload query. MCP protocol
metadata is checked by the existing protocol layer but never forwarded to
workload handlers. HTTP MCP header-selected requests must receive the scope
gate before body reads as existing scoped tools do.

For recognized workload requests, use a server-generated HTTP request-ID
header, never echo the supplied one. Workload audit is only the exact fields
event=workload_read, operation=node_workloads, status, ok, error_code (fixed
or null), elapsed_ms (finite nonnegative bounded number). No remote address,
caller identifier, raw tool spelling, arguments, context, idempotency key,
credentials, paths or exception text. Apply this mode to MCP header-selected
denials before body reads and to recognized REST bodies. Reset request-local
mode on keepalive; an unrelated request must not inherit it. An unauthenticated
or malformed generic REST request that cannot yet identify a tool remains
an ordinary protocol attempt and cannot reach collection.

JSON-RPC correlation is the single protocol-only exception to the application
envelope exclusion: echo id once, only in the outer JSON-RPC wrapper. Accept
an exact non-boolean int within -(2**53-1)..2**53-1 or a1..96-character ASCII
string matching the existing safe request-ID grammar. Invalid IDs return
id:null and a fixed invalid-request protocol error before collection. Never
copy id into structuredContent, content text, metadata, audit, source inputs
or storage. Fixed MCP result wrappers may include only server-owned protocol
metadata and the same application envelope. Workload protocol failures must
not echo caller version/clientInfo/context/metadata or raw parser errors;
sanitize those failures to fixed messages/codes while retaining only the
validated outer correlation ID. Do not rewrite the ordinary MCP protocol or
its unrelated errors.

Extend source, auth, parsing, lifetime and privacy tests on real make_server
instances with ephemeral loopback sockets, temporary owned stores, injected
clocks/readers and event-controlled work. This is ordinary regression proof;
the batch's formal adversarial/acceptance pass remains deferred.

**Acceptance criteria:**

- Actual server construction binds exactly one persistent collector to the same OperationStore and optional explicit readers; repeated reads do not construct stores or collectors, and close/bind-failure cleanup is bounded.
- Legacy, missing, media-only, malformed-policy and wrong-scope credentials cannot invoke collection; allowed_operations and sealed declaration/callback rules cannot be bypassed by hyphen/underscore spellings or injected catalogs.
- REST and controller MCP produce the same canonical application data and fixed errors; invalid arguments, context/unknown fields, null arguments and all idempotency-header forms refuse before source reads or store writes.
- Partial/unavailable sources remain data with original times and omissions; configured host is unchanged by host filters and no router database, port discovery or SSH path exists.
- Seeded caller IDs, raw exceptions, private strings and metadata are absent from application content, errors and workload audit; only a validated protocol correlation ID appears once in the outer MCP wrapper.
- Keepalive mode reset, protocol errors, invalid correlation IDs, server-generated response IDs, immutable schema parity, worker close and ordinary legacy controller regressions are covered.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_workloads.py tests/test_controller.py tests/test_controller_token_normalization.py -x -q`
- `python -m ruff check anvil_serving/observability/workload_tools.py anvil_serving/control_plane/controller/server.py anvil_serving/control_plane/controller/http.py tests/control_plane/test_controller_workloads.py`

### T012.7: Expose explicit controller workload startup options

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/cli.py, tests/control_plane/test_controller_workload_cli.py
**Dependencies:** T012

Add seven optional controller serve string flags matching the make_server/
serve keywords: --workload-benchmark-db, --workload-media-db,
--workload-recipe-registry, --workload-manifest, --workload-topology,
--workload-router-resource and --workload-router-auth-env. Default every value
to None and forward it unchanged to serve. Reuse --node-id; do not add a
second identity flag, an endpoint override, credential value, source discovery
or per-request collector. Bindings remain the sole source-configuration
validator; missing/invalid optional sources are explicit unavailable and
cannot change ordinary legacy server options. Explain in help that paths are
explicit, the three router options work together, and the auth argument names
an environment variable rather than carrying a token.

Follow existing argparse and top-level tree forwarding; the current
commands/control_plane.py controller serve leaf already dispatches to
anvil_serving.controller and needs no independent option parser. Do not edit
a generated manifest by hand. Final T015/T016 own generated surface/reference
synchronization and documentation publication. This slice starts no server,
reads no credential or source, and changes no live configuration.

**Acceptance criteria:**

- All seven explicit strings reach the server entrypoint exactly, and an omitted option remains None rather than resolving an ambient path or environment value.
- Top-level controller serve help exposes the same options without binding a port or starting a collector; ordinary existing flags retain their values.
- Unknown/missing-value options fail at parsing before server invocation, and no credential-value or endpoint override flag is introduced.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_workload_cli.py tests/test_controller.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/cli.py tests/control_plane/test_controller_workload_cli.py`
- `python -m anvil_serving.cli controller serve --help`

### T013.1: Compose bounded canonical fleet workload results

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/fleet_workload_collection.py, tests/observability/test_fleet_workload_collection.py
**Dependencies:** T012.3, T012.3.1

Add pure normalize_node_workloads(host, query, now, node) and
build_fleet_workloads(hosts, query, now, nodes) in a bounded sibling module.
No I/O, clock reads, transport, workers, topology, storage or callbacks.
hosts is an exact tuple of zero to MAX_NODES unique canonical exact-string
host IDs; nodes is an exact dict with only declared exact-string keys and
NodeResult-or-None values. Reject invalid outer input with fixed WorkloadError
before inspecting node values. Validate query and now through the existing
empty build_node_workloads seam, including the empty-fleet case. Sort node
summaries by host ID, retaining every declared node even without any records.

normalize_node_workloads first validates canonical host/query/time. None means
the existing six-source unavailable fallback. An exact NodeResult must have
the expected host, exact ResultStatus/datetime/tuple header fields, one to six
exact SourceResult entries, and unique exact WorkloadOwner keys. Reject a bad
header, wrong host, duplicate owner or oversized shape as one six-source
INVALID unavailable node; a node timestamp more than30 seconds in the future
is a six-source FUTURE unavailable node. Missing owners remain unavailable.
Use build_node_workloads for strict detached source validation, original
query matching, source-local INVALID/FUTURE isolation and node record cap;
do not duplicate its record/progress/enum validation. Preserve the original
valid node collection timestamp and every unchanged source collection time.
For a source rejected by the builder, construct its fixed empty failure at
the validated original node collection time so a stale node does not become
invalid merely because its new failure envelope would otherwise use now.
Do not relabel old valid records or source observations as fresh. Require the
supplied node status to match the supplied source statuses before source-local
normalization; a forged status is an invalid node header. No serializer may
see unchecked arbitrary input, raw dictionaries or subclass objects.

For a provably excluded query.host, return six COMPLETE empty sources at the
collection time for that declared host, without inspecting its supplied value.
This means complete coverage of the empty filtered query, not observed idle
or healthy; later collection short-circuits that node before transport.
Other unavailable nodes use six fixed unavailable sources and unknown
omissions. Invalid/future node fallback error codes are fixed canonical enums;
no raw exception, object repr, endpoint or arbitrary metadata enters results.

Merge normalized node records in one global newest updated_at descending,
record ID ascending order, retaining at most min(query.limit,AGGREGATE_LIMIT).
Use incremental bounded selection: at most one current node's records plus
the current global selection are candidates, and retained records across
stored node summaries never exceed the aggregate cap. Do not create a list
of MAX_NODES times AGGREGATE_LIMIT records. Preserve each source's original
relative record order, timestamps and error. Each removed record increments
that source's omission exactly once; unknown omissions remain unknown and
MAX_COUNT overflow becomes unknown. A previously COMPLETE source losing a
record becomes PARTIAL, never UNAVAILABLE. Recompute node and fleet status
from the resulting source statuses. Fleet truncation.returned is the actual
retained count; omitted sums final source omissions across all nodes, or is
null if any is unknown or the sum exceeds MAX_COUNT. Empty fleet is COMPLETE
with returned0/omitted0. Do not count source omission both at node and fleet
levels. Return new canonical detached objects, no cached input references.

**Acceptance criteria:**

- Two individually valid full nodes compose without exceeding the aggregate record cap; deterministic global ties, per-source ordering and omission reconciliation hold across input insertion orders.
- Every declared host survives as a sorted summary; empty fleet, host exclusions, missing/invalid/wrong-host nodes and future headers have exact fixed outcomes without reading excluded values.
- Source-local malformed/future data preserves healthy peers; original stale node/source times remain intact, including safe fixed failures within a stale node.
- Known, unknown and overflow omissions are correct after multiple incremental evictions; result status and canonical serialization round-trip exactly without private or arbitrary fields.
- Forged types, duplicate owners/hosts, malformed mappings and oversized shapes fail safely; no list of all fleet records, I/O, clock read, worker, topology or transport is introduced.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_fleet_workload_collection.py tests/observability/test_workload_collection.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/fleet_workload_collection.py tests/observability/test_fleet_workload_collection.py`

### T013.1.1: Keep fleet source validation anchored to receipt time

**Feature:** F003
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/fleet_workload_collection.py, tests/observability/test_fleet_workload_collection.py
**Dependencies:** T013.1

Correct the reproduced T013.1 receipt-clock defects without changing the schema,
global record cap, source ownership or other task boundaries. An invalid/naive
node datetime must fail strict normalization; never substitute now and call
its header valid. Invalid node headers use only trusted receipt time for their
six-source INVALID fallback. A wrong-host/future header cannot inject its
untrusted timestamp into the fleet fallback and raise out of composition.

For a valid header, use trusted receipt now when calling build_node_workloads
to validate source skew, source records and the recent-work query. Do not pass
remote node collection time as its now: a node+29s/source+59s pair must reject
the source, not combine two30-second skew allowances. Reconstruct the returned
node with the original valid node collection timestamp. Preserve every unchanged
source timestamp; only missing or newly rejected sources get a fixed empty
failure at the original node time. Rejected-source recognition must be bounded
and safe for forged input, never serialize unchecked originals or echo failures.
Also check each normalized source's collection time against the original node
time: if a forged source is more than30 seconds ahead of its node, isolate it
as FUTURE at node time while preserving healthy peer sources. This check is
timestamp-only, not a second application of the recent-work filter using the
remote clock. Recompute canonical status and keep failures source-local.

**Acceptance criteria:**

- The literal node+29s/source+59s receipt probe becomes a source-local FUTURE failure and keeps an unchanged healthy peer; exactly30 seconds remains allowed.
- A recent-work boundary is evaluated at receipt now rather than a stale/advanced node clock, without rewriting original valid node/source observation times.
- Forged naive/invalid node datetimes and wrong-host future nodes produce fixed node failures without throwing from the fleet composer or relabeling them valid.
- Missing or newly rejected sources in a stale valid node get compatible fixed timestamps, unchanged canonical unavailable sources keep their original timestamp, and forged source-ahead-of-node failures stay source-local.
- Existing global selection, source ordering, known/unknown/overflow omissions, detached serialization and no-I/O behavior remain unchanged.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_fleet_workload_collection.py tests/observability/test_workload_collection.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/fleet_workload_collection.py tests/observability/test_fleet_workload_collection.py`

### T013.1.2: Remove quadratic fleet summary reconstruction

**Feature:** F003
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/fleet_workload_collection.py, tests/observability/test_fleet_workload_collection.py
**Dependencies:** T013.1.1

Replace T013.1's repeated reduction of every earlier node on each new host.
The1000-node empty-fleet probe took8.813 seconds before any network call and
reconstructed roughly three million source summaries. Keep the same public
normalizer/composer signatures and canonical output contract; do not change
the schema, max nodes, deadlines, selection order or omission semantics.

Normalize each input node once. Maintain per-call lightweight node/source
metadata containing only already-validated scalar fields, omission counters
and selected-record references. Use one bounded global top-K structure;
when replacing a selected record, update only its owning source's retained
set and omission counter. Newly discarded records increment their source
omission exactly once. Do not hold an original full NodeResult or SourceResult
inside metadata after it has been processed, because that would retain all
discarded records even if the selection is bounded. Store at most the global
record cap plus one current normalized node and bounded selection bookkeeping.
No persistent cache, secondary registry, mutable public result or new runtime
dependency. A heap or bounded ordered selection may be used, but all time/ID
comparisons remain exact and deterministic; do not use float timestamps or
platform-dependent datetime.timestamp for ordering.

Construct canonical source and node summaries only once at finalization in
sorted host order. Selected records preserve each source's original relative
order. Preserve timestamps, error codes, known/unknown/overflow omissions and
status transitions exactly, including repeated cross-node evictions. Empty
host summaries require linear metadata work, not a quadratic prefix rebuild.
Outer shape validation must also use a bounded host-ID set instead of repeated
linear membership scans while retaining the exact canonical tuple/dict API.

**Acceptance criteria:**

- A deterministic counter around SourceResult.__post_init__ proves at most64*MAX_NODES source constructions for1000 empty declared nodes, with every host still present and the same unavailable/unknown-omission output.
- Existing canonical bytes, global ordering/ties, source-local failures, host exclusions, original timestamps and known/unknown/overflow omissions remain unchanged.
- Repeated evictions touch only affected metadata and no original full-node/source reference retains discarded records beyond the bounded current input.
- The same1000-node empty probe is rerun and measured; tests use structural work-count bounds rather than a flaky wall-clock threshold as the regression gate.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_fleet_workload_collection.py tests/observability/test_workload_collection.py tests/observability/test_workloads.py -x -q`
- `python -m ruff check anvil_serving/observability/fleet_workload_collection.py tests/observability/test_fleet_workload_collection.py`

### T013.2: Read canonical workloads through expected-node controller transport

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/probes/controller_workloads.py, tests/observability/test_controller_workload_source.py
**Dependencies:** T012, T013.1, T013.1.1, T013.1.2

Add read_controller_workloads(endpoint, auth_env, host, query, now, *,
environment=None, monotonic=time.monotonic, _open=None), returning a canonical
NodeResult. Use ControllerTransport, not another HTTP operation protocol,
telemetry detail serializer, SSH fallback or raw controller dictionary.
Validate host/query/time through the existing empty node builder before
configuration, environment, clock or network access. A provably excluded
query.host returns the canonical COMPLETE empty filtered node without those
accesses; other invalid canonical arguments raise fixed WorkloadError.

Configuration is explicit. endpoint is an exact bounded ASCII string of at
most2048 characters with no surrounding whitespace or controls; reuse the
ControllerTransport endpoint and literal private/loopback/tailnet safety
checks before any network operation. auth_env is an exact uppercase ASCII
environment name matching the existing transport grammar, capped at256.
environment=None reads only that key from os.environ; an injected Mapping
is hermetic. Capture and normalize that one credential using the established
authorization normalizer, require16..4096 ASCII characters after normalization,
and pass a new single-value mapping to ControllerTransport. No dotenv, whole
environment copy, inbound policy lookup, endpoint discovery or loopback alias
rewriting. Bad/missing config or credential returns fixed UNAVAILABLE.

Construct a fresh ControllerTransport for this one node call, with exact
expected_node=host, allowed_operations=(node-workloads,), two-second socket
budget and existing MAX_RESPONSE_BYTES ceiling. Call Operation(node-workloads,
canonical seven-field query arguments, tool_name=node_workloads), never with
idempotency or transport context. Expected-node GET /health must precede the
workload POST every time; do not cache identity across queries or transport
instances. Use the shared no-proxy/no-redirect opener unless a test opener
was explicitly supplied. No lifecycle, health-of-model, retry or other call.

Wrap only this transport's opener with one two-second absolute budget measured
from the first validated monotonic value. Before each open and bounded read,
and after it returns, require an exact finite nonnegative int/float clock that
has not regressed or reached the deadline. Pass each open the lesser of its
requested timeout and the remaining budget, so a slow health check cannot
grant the POST a fresh two seconds. No request starts after the deadline.
The wrapper owns response cleanup; guard close failures and close HTTPError
responses without reading their failure bodies. All such failures map to
fixed UNAVAILABLE, with no raw transport details. Do not alter the generic
transport or unrelated telemetry behavior. A socket timeout is not a thread
cancellation guarantee: the later persistent fleet coordinator enforces the
two-second result deadline even for a blocked/dripping test opener and never
spawns replacement workers for those calls. This synchronous reader creates
no thread, timer, queue or executor itself.

After transport success require exactly ok:true and data in its application
envelope; reject additional outer fields, raw context or malformed shapes.
Decode data only through node_result_from_dict, then normalize through
normalize_node_workloads(host,query,now,node). The expected host must match
even with no records. Preserve canonical partiality, source errors, source
times and omissions; malformed node wire data is a fixed INVALID node,
unsupported schema a fixed UNSUPPORTED node and future timestamps a fixed
FUTURE node. A transport/auth/cleanup/clock failure is UNAVAILABLE. Every
failure node contains exactly the six fixed empty owner results and unknown
omissions; never copy exception messages, endpoint/auth references, response
headers, health data, request IDs or raw response material into it. Expose
only read_controller_workloads publicly.

**Acceptance criteria:**

- A fake opener observes exact authenticated health-then-node_workloads requests, declared expected node, canonical query fields and no idempotency/context; a wrong health identity prevents the POST.
- One shared deadline shrinks the POST timeout after health, refuses expired/regressed/nonfinite clocks and rejects late reads without starting replacement work or reading HTTP failure bodies.
- Missing, malformed or excluded configuration/query inputs stop before their specified environment/clock/network boundary, including a hermetic empty environment.
- Canonical complete/partial/unavailable nodes round-trip with original times and omissions; wrong empty-node host, extra envelope fields, malformed/unsupported/future data and throwing cleanup have fixed privacy-safe outcomes.
- No raw transport detail, seeded credential/private value, telemetry serializer, SSH fallback, discovery, generic transport mutation or per-call thread is introduced.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_controller_workload_source.py tests/observability/test_fleet_workload_collection.py tests/observability/test_remote_controller.py -x -q`
- `python -m ruff check anvil_serving/observability/probes/controller_workloads.py tests/observability/test_controller_workload_source.py`

### T013.3: Bound persistent fleet workload collection

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/fleet_workload_collector.py, tests/observability/test_fleet_workload_collector.py
**Dependencies:** T013.1, T013.1.1, T013.1.2

Add FleetWorkloadCollector(readers, *, monotonic=time.monotonic), with
collect(query,now) and idempotent nonblocking close(). readers is an exact dict
of at most MAX_NODES unique canonical host strings to trusted
(host,query,now)->NodeResult callbacks or None; copy it at construction and
perform no source reads, topology/config discovery or worker start then.
Use build_fleet_workloads for argument validation and fixed fallback before
reading clocks or starting workers. No generic plug-in registry, persistent
workload store, result cache, automatic recovery or topology reload.

Own at most four lazily started persistent daemon workers for the collector's
lifetime, not four new threads per request. Only one collect call may be active;
a concurrent or closed collect immediately returns canonical unavailable node
summaries (with provable host-filter exclusions complete/empty). Keep one
active bounded host tuple/index, at most four claimed jobs and four completion
slots, and one bounded canonical accumulated result. No queue or results for
abandoned collection IDs. A worker that remains blocked keeps its slot; never
replace it or accumulate per-node workers. A host already running for an old
collection is unavailable in a new collection and is not invoked concurrently.
Callbacks for None or provably excluded hosts are never scheduled.

The aggregate observation deadline is five seconds after valid monotonic start;
each job's result deadline is the earlier of that and two seconds after the
worker claims the job. Assign sorted eligible hosts to available workers.
Check active generation, close state and deadline before starting a callback.
Run all callbacks, canonical validation/merge and injected clock functions
outside the coordination lock. Catch a throwing reader as fixed unavailable;
normalize its returned node before retaining it. Accept only results completed
before both deadlines, not merely requests started on time. Drop late results.
A completed late call may free its existing worker for another eligible host
while the current aggregate deadline still permits it; never retry its host.
Every unstarted, skipped-busy, missing, timed-out or throwing node remains an
explicit canonical unavailable summary. Sleeping hosts are not awakened.

Collection consumes bounded completion slots and folds each timely node into
the canonical accumulator with build_fleet_workloads over only processed host
summaries. Previous source omissions remain attached, so incremental eviction
counts each removed record once. Retain at most the global1000 records in
accumulated summaries, plus four bounded worker completions and the one current
merge input; never retain all full node results for the final merge. At return,
compose all configured hosts once, filling unobserved hosts with None. Stable
global ordering, node/source times, source-local partiality and omissions are
owned by T013.1, not reconstructed by this coordinator.

The waiter never joins workers and never waits beyond its remaining aggregate
budget. Stop scheduling at expiry and perform only the bounded canonical
finalization; tests allow ordinary local scheduling/serialization overhead,
not an extra socket timeout or executor shutdown. Require exact finite
nonnegative monotonic values with no observed regression; clock/start failures
abandon this collection with fixed unavailable output and clear its queued
work/completion tracking. A late worker must not recreate abandoned metadata.
Do not hold a lock across clock calls or merge. close abandons pending jobs,
clears retained results, wakes waiters, and returns without waiting for blocked
callbacks; once closed, no queued callback may begin. Preserve healthy-node
progress when a different host is slow in a later collection.

**Acceptance criteria:**

- Construction is inert; sequential/repeated/concurrent reads create at most four persistent workers and retain no unbounded job, result or generation history.
- Event-controlled blocked readers cannot exceed concurrency, cause a same-host overlap, block healthy later reads, extend the aggregate wait through executor shutdown or run queued callbacks after close/deadline.
- Two-second per-node result expiry and five-second aggregate expiry are independent; late results are discarded and every unstarted node is explicit, including fleets larger than concurrency.
- Canonical partiality, order, original timestamps and exact/unknown omission accounting survive incremental accumulation and global record eviction without storing all full node results.
- Throwing/invalid clocks, failed worker start, invalid queries, source failures and cleanup preserve fixed no-echo results and bounded metadata; ordinary tests use synthetic readers only.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_fleet_workload_collector.py tests/observability/test_fleet_workload_collection.py -x -q`
- `python -m ruff check anvil_serving/observability/fleet_workload_collector.py tests/observability/test_fleet_workload_collector.py`

### T013: Add bounded expected-node fleet fan-out

**Feature:** F003
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/observability/fleet_workload_sources.py, tests/observability/test_fleet_workload_sources.py
**Dependencies:** T012, T013.1, T013.2, T013.3

Bind the preceding pure composer, expected-node reader and persistent
coordinator through two explicit entrypoints in a new sibling module:
build_fleet_workload_readers(topology, *, environment=None,
monotonic=time.monotonic) and create_fleet_workload_collector(topology_path,
*, environment=None, monotonic=time.monotonic). No CLI/controller/MCP/dashboard
registration occurs here. Consumers own one collector for their process/server
lifetime and must close it; a one-shot CLI may construct/use/close one once.

The builder accepts only an exact Topology with exact tuple hosts, runtimes
and transports. Validate zero to MAX_NODES exact Host objects with unique
canonical host IDs, and at most4*MAX_NODES exact Runtime and Transport objects
with unique canonical IDs, before reading the environment. Invalid structure
raises fixed ValueError with no input material. Other topology collections
are not workload routing inputs. Emit one mapping entry for every declared
host, not just reachable hosts or hosts with qualifying transports. This is
declared inventory, not discovery, hostname detection or physical-health proof.

A host is readable only when exactly one of its declared kind=controller
transports explicitly allows the normalized node-workloads operation. Require
transport.expected_node == transport.host == that host ID, a declared runtime
whose host matches, and explicit endpoint/auth_env. A missing, ambiguous,
wrong-identity or incomplete binding maps only that host to None; never pick
the first transport, borrow another runtime/token, infer an expected node,
use an SSH transport, skip a sleeping host or fall back to another endpoint.
Other declared operations do not grant workload access. Permit the existing
hyphen/underscore operation spelling normalization, never arbitrary aliases.

Capture only each selected transport's auth_env value at construction.
environment=None uses os.environ; injected Mapping is hermetic. Invalid or
throwing references disable only that host and cannot make the builder copy
the whole environment or resolve dotenv. Each callback retains a new
single-value mapping, the exact endpoint/reference and configured host, not
a mutable topology or environment object. It validates incoming canonical
query/time and exact host equality before delegating to T013.2; a provable
host exclusion performs no network/clock read. Source exceptions become the
fixed canonical unavailable node. No network, owner construction, workers,
file reads or lifecycle action occurs while building callbacks.

The factory accepts an explicit exact nonempty string path of at most4096
characters, without controls, surrounding whitespace or leading tilde. Pin
os.path.abspath once at construction, then use load_topology on only that
path and the builder above. No config_home/default path search, overlay
discovery, environment-derived topology path or cwd probing. Invalid/missing
configuration raises a fixed workload-source-unavailable WorkloadError; do
not return an empty COMPLETE fleet when declared inventory could not be read.
Instantiate FleetWorkloadCollector with those exact captured callbacks and
the supplied monotonic seam. Startup is inert apart from this explicit
topology read and bounded environment capture. The factory exposes no raw
topology, address, path, credential or transport object in public results.

Use real parsed generic topology fixtures and an injected controller opener
at the owner-reader seam to prove integrated expected-node reads and healthy
peer survival. T013.2/T013.3 own the per-node/aggregate deadlines and worker
caps; do not reimplement transport, scheduler, serializer or selection here.

**Acceptance criteria:**

- Every declared host appears, including no-transport, sleeping and ambiguous bindings; only an exact same-host/runtime expected-node controller operation is callable and no first-match or SSH fallback exists.
- Builder construction reads no files or network and starts no workers; factory reads only its explicit once-pinned topology path and missing inventory is unavailable rather than an empty complete fleet.
- Captured topology and credentials are isolated from later mutation, injected environments are hermetic, and malformed host/runtime/transport shapes fail before environment access without echo.
- Integrated synthetic controller reads preserve healthy peers and canonical partiality, source times, omissions and fixed wrong-node/schema/future/timeout failures through the bounded coordinator.
- Repeated reads reuse the owned collector and its worker cap; no raw address, token, response, path, transport dictionary, exception or mutation authority crosses the canonical envelope.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_fleet_workload_sources.py tests/observability/test_controller_workload_source.py tests/observability/test_fleet_workload_collector.py -x -q`
- `python -m ruff check anvil_serving/observability/fleet_workload_sources.py tests/observability/test_fleet_workload_sources.py`

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

### T014.1: Seal the fleet controller workload operation

**Feature:** F004
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/observability/workload_tools.py, anvil_serving/control_plane/controller/server.py, anvil_serving/control_plane/controller/http.py, tests/control_plane/test_controller_workloads.py, tests/control_plane/test_controller_fleet_workloads.py
**Dependencies:** T012, T013

Extend the existing sealed node-workload path to exactly two reserved names,
node_workloads and fleet_workloads. Do not register either in the standalone
static MCP catalog: local stdio has no authenticated workload principal and
its generic call path does not enforce requiredScope. Supported MCP delivery
is the controller's authenticated /mcp endpoint and the modern bridge that
dynamically consumes that controller's tools/list and tools/call. The legacy
Python proxy is not a workload surface in v1; leave unrelated tools intact.

In workload_tools.py add FLEET_WORKLOADS_TOOL_NAME, a fresh
fleet_workloads_declaration and an exact-declaration predicate beside the
node equivalents. Share one canonical seven-field schema: required is an
empty list, additionalProperties is false, maxProperties is 7, and the
existing enums, host grammar, numeric bounds and defaults remain unchanged.
Update the node declaration's exact-schema fixture accordingly. Reuse
parse_node_workload_query for both names; its name is compatibility, not a
second query contract. Extend workload_success to exact NodeResult or exact
FleetResult and their canonical serializers. Keep the fixed failure codes;
do not serialize arbitrary duck-typed objects or exception details.

Add optional workload_fleet_topology to make_server and serve, default None.
It is separate from workload_topology, which binds a node's router source.
Only an explicit fleet path may construct one persistent collector through
create_fleet_workload_collector, using the existing injected env and
workload_monotonic seams. Invalid/missing explicit configuration leaves the
fleet collector disabled and legacy controller behavior available; it never
creates an empty COMPLETE inventory or discovers a default. Pass the
collector to make_handler and expose it as anvil_fleet_workload_collector
beside the existing node attribute. Close both collectors on handler/bind
failure and normal server_close, even if one close raises. No per-request
construction, topology reload, source mutation or worker replacement occurs.

Generalize the existing sealed HTTP path, not the generic dispatcher, across
REST /tools/call and controller /mcp. Canonical normalization accepts the
existing underscore/hyphen spelling only. One identical caller-supplied
declaration per reserved name is allowed; a mismatch or duplicate fails
without echo. Existing allowed_operations hides/refuses disallowed names.
Absent collectors remain sealed: authorized calls receive the fixed
workload_source_unavailable failure and cannot fall through to call_tool_func.
Scope workloads:read is checked before the query clock or collector. Retain
the exact REST outer fields name/arguments, the existing bounded MCP
protocol wrapper and ignored protocol _meta, seven query fields, and refusal
of every idempotency-header presence. Host is only a query filter, never a
target/context override. No generic OperationStore request path, mutation,
context hook or transport serializer is allowed for either reserved name.

Success is exactly ok/data with the canonical node or fleet value. Keep the
existing fixed workload application/protocol/auth failures and generated
HTTP request IDs; a validated MCP correlation stays only in the JSON-RPC
wrapper. Track the selected reserved name as closed per-request metadata so
the existing six-field workload audit records the correct fixed operation,
status, ok, error_code, elapsed_ms and event. Reset it on keepalive. Never
copy request labels, remote addresses, arguments, origins, topology paths,
credentials, response bodies or exception text into response/audit data.

**Acceptance criteria:**

- Exact fresh node/fleet declarations share all seven canonical query fields and bounds; duplicate/conflicting declarations fail closed and allowed_operations retains its authority.
- REST and direct controller MCP preserve canonical record bytes, statuses, source timestamps and omissions, and both deny unauthenticated, legacy, media-only and wrong-scope callers before clock/collection.
- Fleet configuration is explicit and independent of node router topology; absent/invalid config is unavailable, repeated requests reuse one collector, and handler/bind/shutdown cleanup closes both collectors.
- Both reserved names bypass generic tool, store, idempotency and context callbacks; missing collectors never fall through and query host cannot replace declared identity.
- Fixed per-request audit operations and generated HTTP IDs survive keepalive and all tested failures without seeded private operands, paths, credentials or exception text.
- The standalone static MCP catalog remains unchanged; scoped controller MCP remains the sole server-side workload authority.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_fleet_workloads.py tests/control_plane/test_controller_workloads.py tests/test_controller.py tests/test_controller_token_normalization.py -x -q`
- `python -m ruff check anvil_serving/observability/workload_tools.py anvil_serving/control_plane/controller/server.py anvil_serving/control_plane/controller/http.py tests/control_plane/test_controller_workloads.py tests/control_plane/test_controller_fleet_workloads.py`

### T014.2: Expose the explicit fleet collector startup option

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/control_plane/controller/cli.py, tests/control_plane/test_controller_workload_startup.py
**Dependencies:** T014.1

Extend the existing controller serve parser with exactly one optional
--workload-fleet-topology PATH, default None, forwarded exactly once to
serve(workload_fleet_topology=...). No new environment default, path lookup,
source read, policy change or collector construction belongs in the parser.
This path enables declared fleet aggregation; --workload-topology remains
only the node router-source topology. Preserve the seven existing startup
options, scoped authorization option and legacy parser defaults. Extend the
existing injected-serve startup tests and the real top-level focused help
probe. Generated manifest/reference synchronization remains T015/T016.

**Acceptance criteria:**

- Direct and top-level controller serve help expose the new optional path with its fleet-only meaning; defaults pass None and an explicit operand is forwarded unchanged once.
- The existing node-source options remain independent and byte-preserved; parser/help tests perform no source, network, credential or lifecycle operation.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_workload_startup.py tests/test_controller.py tests/test_cli.py -x -q`
- `python -m ruff check anvil_serving/control_plane/controller/cli.py tests/control_plane/test_controller_workload_startup.py`
- `python -m anvil_serving.cli controller serve --help`

### T014: Verify the scoped controller MCP workload contract

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** tests/control_plane/test_controller_chaining.py, tests/control_plane/test_controller_fleet_workloads.py
**Dependencies:** T014.1, T014.2

Verify the integrated node/fleet read-only operations over direct scoped
controller MCP and its dynamic tools/list contract. T014.1 owns production
wiring; do not repeat it or add unauthenticated standalone tools. Add the
cross-surface/chaining regression proving a controller-scoped workload token
gets only authorized declarations, canonical application data has no target
context or mutation callback, and a caller-supplied context is rejected
without invocation. The modern bridge forwards this same controller
declaration/call path; the legacy Python proxy and local static catalog do
not expose workload tools in v1. Require workloads:read at the authority
boundary; no local collector can substitute for that authentication gate.

**Acceptance criteria:**

- Controller and MCP return schema-equivalent canonical records for the same query.
- Unauthenticated, legacy, media-only, and wrong-scope principals are denied before collection.
- Tool schemas have only the reviewed filters and contain no operation capable of mutation.
- Errors and chaining context contain no endpoint, credential, raw response, path, or exception.

**Verification:**

- `python scripts/run_tests.py tests/control_plane/test_controller_chaining.py tests/test_controller.py -x -q`
- `python -m ruff check tests/control_plane/test_controller_chaining.py tests/control_plane/test_controller_fleet_workloads.py`

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
