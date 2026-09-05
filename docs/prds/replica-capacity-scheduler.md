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
- R002: A member is eligible only when its backend exists, exact runtime identity is ready, tier and member admission are not quiesced, and its local active reservation count is below its declared member ceiling.
- R003: Request-wide context, tool, media, output, authentication, and tier admission checks must complete before member selection, and failed checks must create no member reservation.
- R004: Candidate snapshot, deterministic selection, rotating tie-break advancement, and member lease reservation must occur atomically under one bounded tier scheduler lock so concurrent requests cannot all observe the same stale local count.
- R005: Local pressure must include active router reservations divided by the member concurrency ceiling, with the ceiling validated as a positive integer and never inferred from an engine name or metrics endpoint.
- R006: When available, upstream pressure may include normalized running requests, waiting requests, scheduler capacity, and KV-cache utilization produced by the existing metrics adapter; raw vendor labels must not enter the scoring function.
- R007: Every upstream pressure sample must carry observed time, success state, and freshness; missing, failed, non-finite, negative, or stale values must become an explicit unknown state that cannot rank ahead of a fresh low-pressure member solely because data is absent.
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
- A stale, failed, malformed, or missing metrics snapshot is represented as unknown and cannot beat a fresh zero-pressure member; if all members have unknown upstream metrics, deterministic local-pressure scheduling still proceeds.
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
**Likely files:** anvil_serving/router/admission.py, tests/router/test_admission.py

Add immutable member snapshots and exactly-once member leases under one tier-owned condition/lock. Preserve the existing tier API for direct routes. Acquire tier and member capacity in one critical section, and notify both member and tier drain waiters on release. Executor guidance: mirror `AdmissionLease` context-manager and idempotent-release behavior; inject no I/O; write race tests with barriers/events instead of sleeps.

**Acceptance criteria:**

- Concurrent acquire never exceeds member or tier ceilings.
- Tier active equals the sum of member active values after every completed test phase.
- Member and tier quiesce/drain affect only their documented scope.
- Every lease can be released repeatedly without decrementing twice.

**Verification:**

- `python scripts/run_tests.py tests/router/test_admission.py -x -q`
- `python -m ruff check anvil_serving/router/admission.py tests/router/test_admission.py`

### T002: Implement a pure deterministic replica scheduler

**Feature:** F002
**Priority:** high
**Type:** feature
**Likely files:** anvil_serving/router/replica_scheduler.py, tests/router/test_replica_scheduler.py
**Dependencies:** T001

Create immutable candidate, pressure, score, and decision value objects plus a pure ranking function. Put only lock/cursor orchestration in a small scheduler class. Define freshness classes and conservative unknown ordering as named constants or enums, not magic numbers. Executor guidance: start with table-driven tests covering every score dimension and permutation; member IDs are tie-break data only and must not influence eligibility or pressure.

**Acceptance criteria:**

- Ranking exactly implements the documented lexicographic order.
- Permuting candidate input does not change the chosen member for the same cursor state.
- Equal candidates rotate fairly and deterministically.
- NaN, infinity, negative values, absent data, and stale data normalize to explicit unknown without exceptions.

**Verification:**

- `python scripts/run_tests.py tests/router/test_replica_scheduler.py -x -q`
- `python -m ruff check anvil_serving/router/replica_scheduler.py tests/router/test_replica_scheduler.py`

### T003: Add cached single-flight normalized member pressure

**Feature:** F003
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/model_capacity.py, tests/router/test_model_capacity.py, tests/router/test_replica_scheduler.py
**Dependencies:** T002

Wrap the existing bounded metrics fetch with a per-member cache and single-flight refresh using injected monotonic time. Produce only the normalized snapshot consumed by the scheduler. Do not hold the scheduler/admission lock while fetching and do not surface raw metrics. Executor guidance: follow existing bounded-body, finite-number, and timeout patterns; use fake clocks and blocking test collectors controlled by events.

**Acceptance criteria:**

- Concurrent refreshes for one member cause one metrics request.
- Fresh cache hits perform no I/O; expiry produces one new bounded refresh.
- Timeout, parse failure, missing series, and non-finite values become typed unknown snapshots.
- Members have independent caches and one slow member does not block another.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_capacity.py tests/router/test_replica_scheduler.py -x -q`

### T004: Integrate one-shot scheduling into the routing lifecycle

**Feature:** F002
**Priority:** high
**Type:** modify
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/backends/relay.py, anvil_serving/router/backends/sse.py, anvil_serving/router/front_door.py, tests/router/test_model_routes.py, tests/router/test_backends.py, tests/router/test_streaming_relay.py
**Dependencies:** T001, T002, T003

Construct the candidate snapshot after all request-wide gates, acquire the selected member lease atomically, and invoke exactly that backend. Attach lease release to every ordinary-response and streaming terminal path with `try/finally` or the existing close callback idiom. Executor guidance: do not loop around backend invocation; make backend fakes count calls; test cancellation and disconnect explicitly because normal status-code tests do not cover lease lifetime.

**Acceptance criteria:**

- Selection occurs once per admitted request and only eligible members are callable.
- All terminal paths release one lease exactly once.
- No upstream failure invokes a second member.
- Direct and scheduler-disabled replica routes retain their existing behavior.

**Verification:**

- `python scripts/run_tests.py tests/router/test_model_routes.py tests/router/test_backends.py tests/router/test_streaming_relay.py -x -q`
- `python scripts/run_tests.py tests/router/test_front_door.py tests/router/test_responses.py -x -q`

### T005: Expose bounded scheduler decisions and capacity state

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** anvil_serving/router/decision_log.py, anvil_serving/router/model_capacity.py, anvil_serving/router/router_telemetry.py, tests/router/test_decision_log.py, tests/router/test_model_capacity.py, tests/router/test_observability_hardening.py
**Dependencies:** T004

Add allowlisted member score, freshness, reservation, and one-attempt fields to existing metadata projections. Keep diagnostics bounded and preserve ring-buffer cardinality. Executor guidance: serialize from typed snapshots rather than object `__dict__`; add explicit negative assertions for URL, address, token, prompt, response, raw metric, and exception substrings.

**Acceptance criteria:**

- One decision record explains which eligible member won and why using normalized fields.
- Capacity snapshots reconcile tier/member reservations and classify telemetry freshness.
- No sensitive or high-cardinality values enter router logs, telemetry, or error bodies.

**Verification:**

- `python scripts/run_tests.py tests/router/test_decision_log.py tests/router/test_model_capacity.py tests/router/test_observability_hardening.py -x -q`

### T006: Document and qualify the capacity-scheduling contract

**Feature:** F004
**Priority:** medium
**Type:** modify
**Likely files:** docs/THIN-CAPABILITY-GATEWAY.md, docs/ARCHITECTURE.md, docs/CONFIGURATION.md, docs/benchmarks/methodology.md, tests/router/test_transition_integration.py
**Dependencies:** T004, T005

Document configuration, score order, freshness, admission, no-replay behavior, and evidence limits. Add a deterministic synthetic routed workload that asserts distribution, member ceilings, errors, and decision records. Define the separately authorized real qualification: managed recipe load, exact identities, matched direct-versus-routed workload, sustained concurrency, failure injection, recorded artifacts, restoration, and human promotion gate. Executor guidance: do not claim the existing direct-to-replica throughput finding proves routed behavior.

**Acceptance criteria:**

- Documentation distinguishes alias resolution, eligibility, scheduling, runtime metrics, qualification, and promotion.
- Synthetic integration tests prove the scheduler contract without hardware or sleeps.
- The real-hardware procedure is reproducible but remains inert until explicitly authorized.
- Router, full test, lint, strict-doc, link, and diff gates pass.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python scripts/run_tests.py tests/ -q`
- `python -m ruff check anvil_serving tests`
- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `git diff --check`
