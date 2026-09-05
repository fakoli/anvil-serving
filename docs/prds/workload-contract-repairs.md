# Project: Workload Contract Repairs

## Summary

Repair two reproduced wire-contract inconsistencies found while integrating workload visibility. These are source fixes within the autonomous router/fleet delivery, not new routing or deployment authority. The existing workload-visibility PRD remains unchanged while its active claims finish.

## Goals

- Default and partially filtered workload reads succeed through the actual scoped controller transport.
- Canonical host validation and dashboard input match the declared 64-character identifier contract.

## Non-Goals

- No new fields, scopes, credential fallback, runtime dependencies, routes, model promotion or live operations.
- No acceptance bypass: source candidates join the consolidated final acceptance batch.

## Requirements

- R001: Controller request serialization omits absent optional owner, kind, state and host fields; explicit invalid null inputs remain rejected by the server.
- R002: Every canonical workload host is an ASCII identifier matching [A-Za-z][A-Za-z0-9_-]{0,63}; 64 characters pass and 65 fail without truncation or normalization.
- R003: Dashboard host input and payload validation enforce the same bound and do not dispatch invalid filters or render invalid host identities.

## Acceptance Criteria

- Default and partial queries work through node and fleet readers against an actual loopback controller with scoped credentials and expected-node verification.
- All 16 subsets of optional filters serialize into a query accepted by the canonical parser with unchanged semantics.
- Host length boundary tests fail on the old implementation and preserve unrelated 1024-character text bounds.
- Full workload/router/controller/dashboard regressions pass before consolidated acceptance.

## Assumptions

### A001: The declared host grammar is authoritative for this unreleased feature.

**Rationale:** workload_tools._declaration and the existing workload-visibility contract already declare 64 characters. Tightening the canonical implementation repairs drift; no operator identifiers are renamed or migrated.

**Requirements:** R002, R003

## Features

### F001: Consistent workload wire contract

Repair request omission and identifier validation with independent producer/consumer regression tests.

**Requirements:** R001, R002, R003

## Tasks

### T001: Omit absent optional controller workload query fields

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/probes/controller_workloads.py, tests/observability/test_controller_workload_source.py

Resolve the query omission defect recorded in .tickets/2026-09-05-workload-wire-contract-repairs.md. In _arguments, always retain active_only, recent_seconds and limit, but add owner/kind/state/host only when present. Both node and fleet readers already reuse this helper. Preserve strict server rejection of explicit null values. Do not widen any parser, transport, scope, budget or response contract. Add a powerset regression covering all optional-field combinations and real loopback controller reads for default and partial queries. Mirror the existing response/clock fixtures while checking the actual serialized POST body. The cross-surface T015 integration test remains in its owning worktree and must receive this source commit before its postcommit proof.

**Acceptance criteria:**

- Serialized default and all 16 optional subsets round-trip through parse_node_workload_query with exact WorkloadQuery equality and no null values.
- Actual expected-node, scoped node and fleet HTTP reads succeed for default and partial filters; the old serializer fails these tests.
- Existing timeout, cleanup, byte limits, response validation and refusal tests remain green.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_controller_workload_source.py -x -q`
- `python -m ruff check anvil_serving/observability/probes/controller_workloads.py tests/observability/test_controller_workload_source.py`

### T002: Enforce the declared canonical host identifier bound

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/workloads.py, tests/observability/test_workloads.py

Resolve the host-bound defect in the same ticket. Tighten the shared _host validation through _HOST_RE to the exact existing declared grammar [A-Za-z][A-Za-z0-9_-]{0,63}; retain fullmatch. Do not change general MAX_TEXT_LENGTH, timestamp, digest, enum or schema-version contracts. Add 64/65-boundary cases for WorkloadQuery, workload_id, WorkloadRecord, NodeResult and FleetResult decoding as applicable, plus parser and controller declaration parity. Do not truncate, normalize or rename identifiers. This task is independent of T001 and does not edit the declaration because it already has the intended bound.

**Acceptance criteria:**

- Exactly 64 legal ASCII characters pass every canonical host seam; 65 fail with WorkloadError.
- Tool declaration pattern and maxLength match canonical host behavior, including whitespace, newline and non-ASCII rejection.
- Other text limits and existing canonical wire fixtures are unchanged.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workloads.py tests/observability/test_workload_collection.py tests/observability/test_fleet_workload_collection.py -x -q`
- `python -m ruff check anvil_serving/observability/workloads.py tests/observability/test_workloads.py`

### T003: Align dashboard host input and payload validation

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** anvil_serving/observability/dashboard/static/index.html, anvil_serving/observability/dashboard/static/workloads.js, tests/observability/dashboard_workloads_ui.cjs, tests/observability/test_dashboard_workloads_ui.py
**Dependencies:** T002

After the workload-visibility GPU-label task releases its overlapping asset, set the host input maxlength to 64 and enforce the exact canonical ASCII grammar in both query validation and node/record host validation. Keep non-host display strings safely rendered as text with their existing independent bounds. Empty host input still means no filter. Malformed or oversized response host identities fail the closed response without showing data. Add executed DOM/fetch tests for 64/65-character inputs and response identities; adapt old unsafe-host display fixtures so malformed host is rejected while valid-host label text remains inert. Preserve credential isolation, no-storage behavior, cancellation, generation fencing, single-flight and polling deadlines.

Synchronize the existing Python markup maxlength assertion.

**Acceptance criteria:**

- A 64-character valid host is sent intact; invalid or 65-character input produces no request.
- Oversized/invalid node or record host identity is rejected without rendering it; valid-host text remains safely inert.
- All existing UI lifecycle and startup tests remain green.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard.py tests/observability/test_dashboard_workloads_ui.py -x -q`
- `git diff --check`

### T004: Preserve the historical read-only dashboard regression contract

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** tests/observability/test_milestone3_dashboard.py
**Dependencies:** T003

The integrated full suite reproduced a stale assertion in test_supported_dashboard_serves_current_history_and_interpretation_read_only: the dashboard now has workload navigation and isolated connect/disconnect controls, so its old total of three buttons is obsolete. Assert the explicit allowed read-only button labels instead of freezing the previous tab count. Retain existing start, stop and restart exclusions and the actual HTTP-backed history/interpretation assertions. No dashboard product behavior changes belong in this task.

**Acceptance criteria:**

- The HTTP-served dashboard exposes only the declared overview, probes, workloads and connection controls.
- Lifecycle mutation exclusions and history/interpretation checks remain intact.
- Historical and current dashboard regressions pass together.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard.py tests/observability/test_dashboard_workloads_ui.py tests/observability/test_milestone3_dashboard.py -x -q`
- `python -m ruff check tests/observability/test_milestone3_dashboard.py`

### T005: Synchronize the dashboard transport omission regression

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** tests/observability/test_workload_http.py, .tickets/2026-09-05-workload-wire-contract-repairs.md
**Dependencies:** T001, T004

The integrated full suite at 0a2e0c69 fails test_default_reader_is_hermetic_and_uses_same_caller_credential because its opener still expects null optional filters. Update that literal expectation to omitted fields, retain default booleans and numeric limits, and validate actual arguments with the independent canonical parser. Preserve the forbidden ambient-environment spy and exact forwarded caller credential checks. Record all five repair candidates and their real test evidence in the existing ticket; keep consolidated acceptance and deployment explicitly pending.

**Acceptance criteria:**

- The actual service-to-controller query matches the strict parser without optional null fields.
- No ambient credentials are read and the caller credential is forwarded unchanged.
- Workload service and transport regression suites pass together.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workload_http.py tests/observability/test_controller_workload_source.py -x -q`
- `python -m ruff check tests/observability/test_workload_http.py`

