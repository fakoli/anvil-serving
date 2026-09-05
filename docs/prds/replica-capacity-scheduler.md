# Project: Capacity-Aware Scheduling for Qualified Replicas

## Summary

Replace simple round robin inside an already selected, qualified same-host replica tier with a deterministic least-loaded scheduler that atomically considers router reservations and fresh normalized upstream pressure. The scheduler chooses only among members admitted by the Qualified Same-Host Replica Sets contract; it never chooses an alias, model, capability, tier, topology host, engine, or deployment recipe. Its decisions are metadata-only, explainable, bounded, and still permit exactly one upstream dispatch attempt.

## Goals

- Prevent avoidable overload by reserving member capacity atomically before dispatch.
- Prefer an eligible member with lower proven pressure while treating stale or unknown telemetry conservatively.
- Reuse the current readiness, admission, capacity, metrics, streaming, and decision-log seams instead of adding a second routing stack.
- Keep the scheduler deterministic and testable with injected time, normalized snapshots, and pure scoring.
- Provide task-sized implementation instructions and independent gates suitable for a focused execution model.

## Non-Goals

- Selecting a caller alias, logical tier, model, capability, host, serving engine, quantization, or recipe.
- Cross-host load balancing, global fleet scheduling, failover, placement, autoscaling, or process lifecycle.
- Predicting request token cost, quality, latency, or semantic difficulty from prompts.
- Retrying, replaying, hedging, or re-deciding after the chosen backend is invoked.
- Reading vendor-specific metrics directly in selection code or preferring a serving-engine brand.
- Treating a configured scheduler ceiling, KV capacity, readiness response, or metrics sample as qualification.
- Changing purpose-model, audio, media, or direct single-endpoint routing.

## Requirements

- R001: Capacity-aware scheduling must run only after one alias has resolved to one replica-enabled logical tier whose members passed the qualified-replica configuration and readiness contracts.
- R002: A member is eligible only when its backend exists, served-model readiness and declared replica provenance pass, tier/member admission is not quiesced, and active reservations are below its positive member ceiling.
- R003: Complete request-wide context, tool, media, output, and authentication gates first. Then atomically check tier/member quiesce and ceilings, select, and acquire one compound tier/member lease; failed checks create neither reservation.
- R004: Candidate snapshot, deterministic selection, rotating tie-break advancement, and member lease reservation must occur atomically under one bounded tier scheduler lock so concurrent requests cannot all observe the same stale local count.
- R005: Local pressure must include active router reservations divided by the member concurrency ceiling, with the ceiling validated as a positive integer and never inferred from an engine name or metrics endpoint.
- R006: When available, upstream pressure may include normalized running requests, waiting requests, scheduler capacity, and KV-cache utilization produced by the existing metrics adapter; raw vendor labels must not enter the scoring function.
- R007: Every upstream pressure sample must carry observed time, success state, and freshness; fresh, stale, failed, and unknown provenance must remain explicit, and every non-fresh class must rank as upstream unknown so absent or unusable data cannot beat a fresh low-pressure member at equal local load.
- R008: The first scheduler must use a documented lexicographic score: eligibility, conservative local pressure, conservative normalized upstream pressure, then a rotating stable member-ID tie break; floating weights, adaptive learning, and prompt-derived costs are prohibited.
- R009: Metrics collection must be time-bounded, cached per member, single-flight under concurrency, and outside the selection lock; request handling may use the latest bounded snapshot but must never wait indefinitely for metrics.
- R010: Once a member lease is reserved and its backend is invoked, no readiness or pressure change may cause a second selection for that request; all terminal success, error, timeout, cancellation, disconnect, and streaming-close paths must release the same lease exactly once.
- R011: Member quiesce and drain must stop and wait for that member only, while tier quiesce and drain must cover all members; snapshot totals must reconcile tier active leases with the sum of member active leases.
- R012: Decision and capacity evidence must expose the selected safe member ID, eligibility set, normalized score components, telemetry freshness class, reservation counts, and one-attempt outcome while excluding URLs, host addresses, credentials, prompts, response bodies, raw metric text, and raw exceptions.
- R013: Existing direct tiers, simple round robin when capacity scheduling is disabled, purpose routes, audio routes, and all wire/dialect behavior must remain unchanged.
- R014: Scheduling logic must remain stdlib-only, use injected clocks and immutable snapshots, avoid sleeps in tests, return structured data from library code, and use bounded cardinality and bounded diagnostic strings.

## Acceptance Criteria

- Two fresh, ready members with local loads 0/2 and 1/2 deterministically select the first; after its atomic reservation produces equal load, the rotating tie break distributes the next selection fairly.
- Twenty concurrent selection attempts never exceed either member's declared ceiling and tier active count always equals the sum of member active counts.
- At equal local pressure, unknown upstream telemetry cannot beat fresh zero pressure. A lower local-pressure member still wins when its telemetry is unknown. If all upstream samples are unknown, local pressure and rotating ties decide.
- A metrics fetch is single-flight and time-bounded; tests with an injected slow collector prove the selection lock is not held during I/O.
- Success, HTTP error, timeout, cancellation, client disconnect, normal SSE end, and malformed SSE each release the chosen member lease exactly once and never invoke a peer backend.
- Member and tier drain semantics are independently proven without cancelling in-flight requests.
- Decision/capacity serialization contains the documented score inputs and freshness classes but none of the prohibited sensitive fields.
- A synthetic routed workload demonstrates distribution and ceiling enforcement; any real-hardware benchmark or promotion remains a separately approved operator gate.

## Risks

- A check-then-reserve implementation would oversubscribe a member under concurrency; selection and lease creation must be one atomic operation.
- Upstream metrics describe runtime state at a point in time and may disagree with router-local reservations; local counts remain the safety floor.
- Engine metrics have different names and semantics; normalization must be bounded in adapters and unknown must remain visible.
- Holding the scheduler lock during health or metrics I/O would serialize requests and create a latency regression.
- Lease leaks on streaming disconnects can permanently exhaust a member; terminal-path tests must be exhaustive.
- Overly clever scoring would be difficult to qualify and could turn infrastructure routing into an unreviewed policy engine.

## Open Questions

None. The first release uses lexicographic conservative scoring, cached normalized upstream pressure, atomic local reservations, deterministic rotating ties, and no post-dispatch retry.

## Assumptions

### A001: Qualified replica sets are implemented and reviewed before this scheduler is enabled.

**Rationale:** Eligibility, exact identity, and same-host membership belong to the prerequisite contract. This project consumes those facts and must not recreate or weaken them.

**Requirements:** R001, R002, R013

### A002: Router-local reservations are the authoritative admission safety signal.

**Rationale:** Upstream telemetry can be delayed, absent, or vendor-specific. Atomic local leases are immediately consistent with requests accepted by this router and therefore remain the conservative floor.

**Requirements:** R004, R005, R007, R010, R011

### A003: Normalized upstream pressure improves ordering but never proves capacity or qualification.

**Rationale:** Existing `model_capacity.py` already distinguishes declared capacity from observed metrics. The scheduler may use fresh signals to choose among already eligible peers without turning metrics into a promotion gate.

**Requirements:** R006, R007, R008, R012

## Closed v1 implementation contract

- Add tier `replica_strategy`: `round_robin` by default, or explicit `capacity`; reject it on direct tiers. Add optional positive integer member `max_concurrency` (1-100000), mandatory for every member in capacity mode. A configured member ceiling is enforced in either mode.
- Tier `max_concurrency` is the aggregate ceiling, never multiplied by member count. If absent, capacity mode is bounded by the sum of member ceilings. Compound admission replaces the independent blocking backend semaphore on replica paths; exhausted capacity returns the existing unavailable response without waiting or reserving another tier.
- Under one tier-owned condition: recheck quiesce and ceilings, combine already-completed immutable readiness/pressure snapshots with current local counts, call the fixed built-in pure rank function, and increment tier/member counts. `TierAdmission` remains the only lock, cursor, and reservation owner; no scheduler object, I/O, caller callback, mutable caller mapping, or clock access runs under that condition.
- `PressureFreshness` is `fresh|stale|failed|unknown`; `PressureSignalState` is `valid|missing|invalid`. Frozen `ReplicaPressure`, `ReplicaCandidate`, `ReplicaScore`, and `ReplicaDecision` values accept only their closed exact field types and bounds. A candidate has safe member ID, exact Boolean eligibility, active reservations 0-1000000000, member capacity 1-100000, and validated immutable pressure. Each score copies freshness from its pressure; every non-fresh class ranks as upstream unknown. Ranking accepts an exact tuple of 2-16 unique candidates and an exact integer cursor from zero through `N-1`; invalid inputs raise fixed `ValueError`, while an empty eligible set is an ordinary decision with `selected_member_id=None` and reason `no-eligible-member`.
- Filter members that are not eligible or whose active reservations meet their ceiling, then rank by `(local_ratio, upstream_unknown, upstream_pressure_ppm, rotating_rank)`. Compare local ratios exactly with integer cross-products, never rounded parts per million. Fresh upstream pressure is the maximum of valid request pressure and valid KV pressure: request pressure is `ceil((running + waiting) * 1000000 / scheduler_capacity)` in the range 0-2000000000000000 ppm without clamping at 1000000, and KV pressure is `ceil(kv_cache_usage_fraction * 1000000)` in the range 0-1000000 ppm. All serialized evidence remains below `2^53`.
- `normalize_replica_pressure(*, observed_at, now_monotonic, successful, requests_running=None, requests_waiting=None, scheduler_capacity=None, kv_cache_usage_fraction=None)` is pure, never retains raw values or errors, and imports no metrics adapter. It accepts only exact built-in `int`/`float` numeric telemetry, never Boolean: integral request counts are 0-1000000000, scheduler capacity is an exact integer 1-100000, and KV utilization is finite and 0-1. `None` means missing; an incomplete request tuple is missing rather than zero; any supplied malformed optional signal makes the whole pressure unknown and wins over missing. Valid KV alone may produce fresh pressure when the request tuple is missing. Unknown pressure has `pressure_ppm=None` plus fixed request/KV signal states.
- Pressure is fresh only when `successful is True`, both monotonic clock values are finite and nonnegative, age is from zero through 5 seconds inclusive, and at least one signal is valid. `successful is False` produces `failed`; a successful otherwise-valid sample older than 5 seconds produces `stale`; missing or invalid signals, future or invalid clocks, and malformed inputs produce `unknown`. All non-fresh states have `pressure_ppm=None` and rank identically as upstream unknown while retaining their freshness class for T011 evidence. T003 adds observation time to its composite-identity cache and passes the existing raw metrics fields `requests_running`, `requests_waiting`, and `kv_cache_usage_fraction` plus the configured member ceiling as `scheduler_capacity`; no engine-specific capacity series is invented.
- Rank returns score rows in winning order and eligible IDs in sorted member-ID order, with reason `selected` or `no-eligible-member`. Rotating rank and the admission cursor always use the full sorted configured membership, independent of candidate input order; the cursor advances only after a successful reservation. `MemberAdmissionLease.selection` is the immutable `ReplicaDecision` for capacity selections and `None` for legacy round robin. Pressure evidence is capped at 16 rows and carries no raw metric text or URL.
- `TierAdmission` accepts copied and validated replica strategy and capacity mappings only for configured replica tiers; capacity strategy requires ceilings for every member. `acquire_member` copies and validates the complete readiness and pressure mappings before entering the condition; absent pressure means unknown, while incomplete, duplicate, unknown-member, or malformed mappings refuse without changing the cursor or counts.
- Existing router `transition-status|quiesce|drain|readmit` verbs gain optional `--member <id>` with required `--tier`; the authenticated transition endpoint uses optional `member_id`. Omission preserves tier behavior. Unknown members/direct tiers refuse before mutation. Member readmission verifies only that member's served-model readiness and never readmits the tier; tier readmission never clears member quiesce. Drain requires quiesce of its scope and cannot cancel in-flight leases.
- Persist member quiesce intent in an optional `members` mapping of tier ID to member ID/reason alongside existing tier intent. Restore only configured IDs, reject malformed state, and retain member intent across restart. Extend `router_manage.py`, front-door transition handlers, command spec/manifest and focused endpoint tests in T004/T006.
- `ReplicaPressureCache(tiers, *, metrics_provider=fetch_vllm_metrics, monotonic=time.monotonic)` owns only explicitly supplied capacity-mode tiers, at most 256 configured members total and 2-16 per tier. Copy their composite `(tier.id, member.id)` registration and member endpoint views once; reject duplicate identities, malformed ceilings, direct/round-robin tiers or overflow with a fixed error. `snapshot(tier_id)` returns a detached complete member-to-`ReplicaPressure` mapping for that configured tier; unknown IDs refuse before scheduling. No caller-supplied endpoint or arbitrary cache key is accepted.
- Pressure snapshot reads never wait for collection. Start at most two lazy daemon workers for the entire cache, not per tier, member, refresh or request. A condition-protected FIFO queue holds at most one pending refresh per registered member; queued and running keys share the same single-flight marker. Refresh after 1 second since completion, retaining the last completed normalized sample while work is pending. Retain only immutable normalized pressure and its monotonic observation time, not raw metrics or errors; a valid cached sample older than 5 seconds is stale. Missing cache is unknown. Two stalled workers may leave other samples stale/unknown but must never create extra workers or unbounded queue growth.
- Every collector uses the existing 1-second transport timeout and 1 MiB-plus-one body bound. A fetch exceeding 1 second of injected monotonic elapsed time is failed, even if it eventually returns valid data; discard its late values. A running overdue fetch projects failed without awaiting it. Transport/HTTP/provider exceptions are failed; parse/oversize/missing/invalid snapshot or series are unknown. Invalid/future clocks produce unknown, never fresh. Read clocks and perform collector/normalizer work outside cache and admission locks. A socket timeout is not a hard cancellation guarantee for a trickling body: the fixed worker bound and nonblocking snapshot contract are the request-latency guarantee.
- `close()` is idempotent and nonblocking: prevent new refreshes, clear queued work, wake idle workers, and ignore late running completions. After close, snapshots return unknown for every registered member. T004 owns one cache per `RoutingBackend` and closes it with the backend/server lifecycle; no threads are started for direct or round-robin-only configurations. Tests use fake monotonic time and events/conditions, never sleeps, including two-worker bounds, blocked-peer progress, deadline expiry, late-result discard, and closed-owner behavior.
- Require event/barrier tests for oversubscription, quiesce versus acquire, single-flight collection, stream close before iteration, collector failure and expiry. Include the decisive counterexample: unknown 0/2 local beats fresh 1/2 local; fresh wins when both local counts are 0/2.

## Code Map

- `anvil_serving/router/serve.py::RoutingBackend` is the single alias resolution and dispatch path. Integrate member selection after request/tier checks and before the one backend invocation.
- `anvil_serving/router/admission.py::TierAdmission`, `AdmissionLease`, and `AdmissionSnapshot` establish lock, quiesce, drain, and idempotent release idioms. Extend by composition for member leases; do not create an unrelated semaphore path.
- `anvil_serving/router/model_capacity.py::MetricsSnapshot`, `fetch_vllm_metrics`, and `build_model_capacity` already bound and normalize capacity telemetry. Generalize adapter output only where necessary, and keep vendor parsing outside scheduling.
- `anvil_serving/router/availability.py` provides member readiness and runtime identity snapshots. Consume a completed snapshot; never probe while holding the scheduler lock.
- `anvil_serving/router/decision_log.py::DecisionLog` and `router_telemetry.py` own metadata-only bounded records and aggregates.
- `anvil_serving/router/backends/relay.py`, `anvil_serving/router/backends/sse.py`, and `anvil_serving/router/front_door.py` own terminal behavior for ordinary and SSE responses. Lease lifetime must cover the complete upstream response/stream lifecycle.
- `tests/router/test_admission.py`, `tests/router/test_model_capacity.py`, `tests/router/test_model_routes.py`, `tests/router/test_backends.py`, `tests/router/test_streaming_relay.py`, `tests/router/test_decision_log.py`, and `tests/router/test_observability_hardening.py` are primary test seams.
- `docs/THIN-CAPABILITY-GATEWAY.md`, `docs/ARCHITECTURE.md`, and `docs/CONFIGURATION.md` must distinguish capacity-aware member ordering from alias/tier selection and from qualification.

## Features

### F001: Atomic member reservations

Introduce member ceilings, quiesce/drain state, and exactly-once leases that reconcile with existing tier admission.

**Requirements:** R002, R003, R004, R005, R010, R011, R014

### F002: Deterministic capacity scheduler

Score only eligible same-host members with conservative local and normalized upstream pressure plus a stable rotating tie break.

**Requirements:** R001, R004, R005, R006, R007, R008, R013, R014

### F003: Bounded pressure sampling

Collect, normalize, cache, and classify member pressure without blocking the selection lock or leaking raw engine metrics.

**Requirements:** R006, R007, R009, R014

### F004: Explainable scheduling evidence

Project safe score components and prove distribution, ceilings, lease release, and no-replay behavior.

**Requirements:** R010, R011, R012, R013

## Tasks

### T001: Extend admission with member-scoped atomic leases

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/config.py, anvil_serving/router/admission.py, tests/router/test_config.py, tests/router/test_admission.py

First implement `replica_strategy` and member `max_concurrency` parsing under the closed contract below. Add immutable snapshots and compound exactly-once leases under one tier-owned condition. Preserve the direct-tier API and avoid the independent backend semaphore on replica paths. Acquire tier/member capacity together and notify drain waiters on release. Test races with barriers/events, not sleeps.

**Acceptance criteria:**

- Concurrent acquire never exceeds member or tier ceilings.
- Tier active equals the sum of member active values after every completed test phase.
- Member and tier quiesce/drain affect only their documented scope.
- Every lease can be released repeatedly without decrementing twice.

**Verification:**

- `python scripts/run_tests.py tests/router/test_config.py tests/router/test_admission.py -x -q`
- `python -m ruff check anvil_serving/router/config.py anvil_serving/router/admission.py tests/router/test_config.py tests/router/test_admission.py`

### T002: Implement a pure deterministic replica scheduler

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/router/replica_scheduler.py, tests/router/test_replica_scheduler.py, anvil_serving/router/admission.py, tests/router/test_admission.py
**Dependencies:** T001

Create the closed immutable pressure, candidate, score, and decision values, pure normalizer, and pure fixed rank function. Do not add a scheduler class, lock, or cursor: extend `TierAdmission` to validate and copy replica strategies/capacities and completed pressure mappings outside its existing condition, then call the fixed rank function and reserve atomically under that condition. Preserve the direct API and legacy round robin; capacity selections attach the immutable decision to the compound lease. Executor guidance: start with table-driven tests covering every score dimension, exact ppm ceiling, signal state, freshness edge, and input permutation.

**Acceptance criteria:**

- Ranking exactly implements the documented lexicographic order, exact local-ratio comparison, unbounded-at-one request ppm, and winning-order score rows.
- Permuting candidate input does not change the decision for the same full-membership cursor, and eligible IDs remain sorted.
- Equal candidates rotate fairly and deterministically, and the admission cursor advances only after a successful reservation.
- Missing and invalid signals retain typed signal state; successful samples older than five seconds become stale, unsuccessful samples become failed, and future clocks or malformed telemetry become unknown without retaining raw values or exceptions.
- Admission validates completed pressure outside its condition, enforces member and tier ceilings atomically, and refuses malformed mappings without changing counts or cursor.

**Verification:**

- `python scripts/run_tests.py tests/router/test_replica_scheduler.py tests/router/test_admission.py -x -q`
- `python -m ruff check anvil_serving/router/replica_scheduler.py tests/router/test_replica_scheduler.py anvil_serving/router/admission.py tests/router/test_admission.py`

### T003: Add cached single-flight normalized member pressure

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/model_capacity.py, tests/router/test_model_capacity.py, tests/router/test_replica_scheduler.py
**Dependencies:** T002

Implement the closed `ReplicaPressureCache` contract with a fixed two-worker owner, bounded registered-member FIFO and single-flight refresh. Normalize the existing `requests_running`, `requests_waiting`, and `kv_cache_usage_fraction` fields with the configured member ceiling as `scheduler_capacity`, retaining only immutable normalized results plus observation time. Snapshot calls never wait for I/O; overdue or late results fail conservatively. Implement idempotent nonblocking close without adding a thread per request, engine-specific capacity metric, or reverse scheduler import. Use fake clocks and blocking collectors controlled by events.

**Acceptance criteria:**

- Concurrent refreshes for one member cause one metrics request.
- Fresh cache hits perform no I/O; expiry produces one new bounded refresh.
- Timeout and collection failure become typed failed snapshots; parse failure, missing series, and non-finite values become typed unknown snapshots.
- Members have independent caches and one slow member does not block another.
- Snapshot calls remain nonblocking under two stalled collectors, queue size never exceeds registered membership, and close prevents refresh or late publication.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_capacity.py tests/router/test_replica_scheduler.py -x -q`

### T004: Integrate one-shot scheduling into the routing lifecycle

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, tests/router/test_model_routes.py, tests/router/test_backends.py
**Dependencies:** T001, T002, T003

Construct completed readiness and pressure snapshots after all request-wide gates, pass them once to admission, acquire the selected compound lease, and invoke exactly that backend. Pass replica strategies and member ceilings at both `RoutingBackend`'s fallback admission construction and `build_server`'s shared admission construction; replica members must not also receive `_ConcurrencyLimitedBackend`, because compound admission owns their ceilings. Keep I/O outside the condition and never loop around backend invocation. Make backend fakes count calls and cover capacity exhaustion and unknown-versus-fresh counterexamples.

**Acceptance criteria:**

- Selection occurs once per admitted request and only eligible members are callable.
- The decisive local-pressure and unknown-telemetry cases select exactly as specified.
- Capacity exhaustion creates no reservation and invokes no backend.
- No ordinary-response failure invokes a second member.
- Direct and scheduler-disabled replica routes retain their existing behavior.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_routes.py tests/router/test_backends.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py tests/router/test_model_routes.py tests/router/test_backends.py`

### T005: Expose bounded scheduler decisions

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/decision_log.py, tests/router/test_decision_log.py, tests/router/test_observability_hardening.py
**Dependencies:** T004

Add allowlisted member score, freshness, reservation, eligibility-set, and one-attempt fields to the existing decision record. Keep diagnostics and candidate rows bounded and preserve ring-buffer cardinality. Serialize from typed snapshots rather than object `__dict__`; add explicit negative assertions for URL, address, token, prompt, response, raw metric, and exception substrings.

**Acceptance criteria:**

- One decision record explains which eligible member won and why using normalized fields.
- Exhaustion and upstream failure retain the one-selection/one-attempt boundary.
- No sensitive or high-cardinality values enter decision logs or error bodies.

**Verification:**

- `python scripts/run_tests.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check anvil_serving/router/decision_log.py tests/router/test_decision_log.py tests/router/test_observability_hardening.py`

### T006: Document the capacity-scheduling contract

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/THIN-CAPABILITY-GATEWAY.md, docs/ARCHITECTURE.md, docs/CONFIGURATION.md
**Dependencies:** T005, T009, T010, T011

Document configuration, exact score order, freshness boundaries, aggregate and member admission, member/tier lifecycle controls, persisted intent, no-replay behavior, and evidence limits. Keep alias resolution, eligibility, scheduling, runtime metrics, qualification, and promotion as distinct concepts. Do not claim the existing direct-to-replica throughput finding proves routed behavior.

**Acceptance criteria:**

- Documentation distinguishes alias resolution, eligibility, scheduling, runtime metrics, qualification, and promotion.
- Member and tier transition syntax, independent readmission, and persisted quiesce semantics are explicit.
- Configuration documents round-robin compatibility and every capacity-mode validation rule.
- No operational or benchmark claim exceeds the synthetic evidence.

**Verification:**

- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `git diff --check`

### T007: Preserve leases across ordinary and SSE terminal paths

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/backends/relay.py, anvil_serving/router/backends/sse.py, tests/router/test_streaming_relay.py, tests/router/test_responses.py
**Dependencies:** T004

Attach the chosen compound lease to the complete ordinary-response and streaming lifecycle using the existing close-aware iterator idiom. Release exactly once on success, HTTP error, timeout, cancellation, disconnect, malformed SSE, normal stream end, explicit close, and close-before-first-iteration. A terminal failure must never trigger another selection.

**Acceptance criteria:**

- Every response and streaming terminal path releases the selected lease exactly once.
- Close-before-first-iteration releases capacity without invoking a peer backend.
- Cancellation, disconnect, timeout, and malformed SSE do not leak capacity.
- No terminal path retries, replays, or selects a second member.

**Verification:**

- `python scripts/run_tests.py tests/router/test_streaming_relay.py tests/router/test_responses.py -x -q`
- `python -m ruff check anvil_serving/router/backends/relay.py anvil_serving/router/backends/sse.py tests/router/test_streaming_relay.py tests/router/test_responses.py`

### T008: Add member-scoped transition backend and endpoint controls

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/front_door.py, tests/router/test_transition_integration.py, tests/router/test_front_door_auth.py
**Dependencies:** T001, T004

Extend transition status, quiesce, drain, and readmit to accept an optional validated member ID while preserving omitted-member tier behavior. Refuse unknown members and direct-tier member operations before mutation. Member readmission probes only that member and never clears tier quiesce; tier readmission never clears member quiesce. Drain requires its scope to be quiesced and cannot cancel in-flight leases.

**Acceptance criteria:**

- Authenticated transition endpoints implement the exact tier/member interaction matrix.
- Unknown members and direct-tier member requests fail before state mutation.
- Member and tier drains wait only for their documented scopes.
- Existing tier-only endpoint behavior and authentication remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_transition_integration.py tests/router/test_front_door_auth.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py anvil_serving/router/front_door.py tests/router/test_transition_integration.py tests/router/test_front_door_auth.py`

### T009: Add member addressing to router transition CLI contracts

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router_manage.py, anvil_serving/commands/router.py, tests/test_router_manage.py, tests/test_cli_contract.py
**Dependencies:** T008

Add optional `--member <id>` to `transition-status`, `quiesce`, `drain`, and `readmit`, retaining required `--tier` and existing confirmation rules. Send the validated member ID through the authenticated transition request and render bounded structured results without endpoint or auth data. Update the command specification/manifest contract rather than creating an alternate CLI.

**Acceptance criteria:**

- All four existing verbs accept member addressing and preserve omitted-member tier behavior.
- Missing tier, unsafe member ID, unknown member, and direct-tier member use fail deterministically.
- Confirmation and dry-run behavior remains unchanged for mutating verbs.
- CLI help and machine-readable command contracts agree.

**Verification:**

- `python scripts/run_tests.py tests/test_router_manage.py tests/test_cli_contract.py -x -q`
- `python -m ruff check anvil_serving/router_manage.py anvil_serving/commands/router.py tests/test_router_manage.py tests/test_cli_contract.py`

### T010: Persist and restore member quiesce intent

**Feature:** F001
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, tests/router/test_transition_integration.py, tests/router/test_serve_cli.py
**Dependencies:** T008, T009

Extend the admission-intent document with the optional closed `members` mapping of tier ID to member ID/reason. Atomically persist member quiesce changes, restore only configured member IDs, reject malformed state, and retain tier and member intent independently across restart. Preserve current handling of tier promotion-owned intent.

**Acceptance criteria:**

- Member quiesce survives restart without changing tier quiesce state.
- Tier readmission does not remove persisted member intent, and member readmission removes only its own intent.
- Malformed member state refuses startup; removed/unconfigured members are not restored.
- Existing tier-only intent documents remain compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_transition_integration.py tests/router/test_serve_cli.py -x -q`
- `python -m ruff check anvil_serving/router/serve.py tests/router/test_transition_integration.py tests/router/test_serve_cli.py`

### T011: Expose bounded capacity and telemetry state

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/model_capacity.py, anvil_serving/router/router_telemetry.py, tests/router/test_model_capacity.py, tests/router/test_router_telemetry.py
**Dependencies:** T003, T004

Project normalized freshness, bounded score components, tier/member reservation counts, ceilings, and aggregate reconciliation through the existing capacity and telemetry surfaces. Consume typed snapshots and exclude raw metric labels/text, endpoint identity, and unbounded member data.

**Acceptance criteria:**

- Capacity output reconciles tier active leases with the sum of member active leases.
- Fresh, stale, failed, and unknown telemetry are explicitly classified.
- Candidate evidence is ordered and capped at the configured 16-member bound.
- Existing direct-tier capacity and telemetry output remains compatible.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_capacity.py tests/router/test_router_telemetry.py -x -q`
- `python -m ruff check anvil_serving/router/model_capacity.py anvil_serving/router/router_telemetry.py tests/router/test_model_capacity.py tests/router/test_router_telemetry.py`

### T012: Prove the integrated scheduler with a synthetic workload

**Feature:** F004
**Priority:** high
**Type:** modify
**Likely files:** tests/router/test_transition_integration.py, tests/router/test_model_routes.py, tests/router/test_observability_hardening.py
**Dependencies:** T005, T007, T010, T011

Add one hermetic event-driven workload that exercises deterministic distribution, concurrent tier/member ceilings, member and tier drains, telemetry freshness, upstream errors, decision evidence, and the one-attempt boundary. Use injected clocks, barriers, and fake backends; use no hardware, network service, or sleeps.

**Acceptance criteria:**

- Twenty concurrent attempts never exceed either member or aggregate tier ceilings.
- Distribution follows exact local/upstream/tie ordering, including both decisive unknown cases.
- Member/tier drain and restart-restored intent retain their independent scopes.
- Error and stream-close cases release capacity once, record bounded evidence, and invoke no peer.

**Verification:**

- `python scripts/run_tests.py tests/router/test_transition_integration.py tests/router/test_model_routes.py tests/router/test_observability_hardening.py -x -q`
- `python -m ruff check tests/router/test_transition_integration.py tests/router/test_model_routes.py tests/router/test_observability_hardening.py`

### T013: Define qualification and run integrated release gates

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/benchmarks/methodology.md
**Dependencies:** T006, T012

Define the separately authorized real qualification: managed recipe load, exact served identities and declared provenance, matched direct-versus-routed workload, sustained concurrency, failure injection, recorded artifacts, restoration, and human promotion gate. Keep the procedure inert in this project, then run every focused, full-suite, lint, strict-doc, link, and diff gate without weakening earlier acceptance criteria.

**Acceptance criteria:**

- The real-hardware procedure is reproducible but performs no live operation without separate authority.
- Existing direct-to-replica evidence is not represented as routed-scheduler proof.
- Synthetic results and real qualification/promotion status remain explicitly distinct.
- Router, full test, lint, strict-doc, link, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `git diff --check`
