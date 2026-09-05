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

### T006: Permit the packaged workload script through the dashboard CSP

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/api.py, tests/observability/test_workload_content_security_policy.py, .tickets/2026-09-05-dashboard-workload-csp.md
**Dependencies:** T005

A real headless Edge smoke at the integrated candidate found script-src 'unsafe-inline' blocks the newly packaged same-origin /workloads.js, leaving the panel disconnected and making zero requests. Add only 'self' to the existing script-src policy. Preserve every other CSP directive, no-store, nosniff, frame denial and authentication boundary. Add actual HTTP tests for the served document/script, parse the CSP directives into exact token sets, and assert the same-origin script is permitted while arbitrary external/eval/data/blob script sources remain absent. Record the reproduced console message and browser retest distinction in a new ticket. No inline-script removal or broader CSP redesign belongs in this repair.

**Acceptance criteria:**

- The actual served document permits its same-origin workloads.js without permitting arbitrary external script origins.
- Existing inline dashboard behavior, frame/object restrictions and response security headers are unchanged.
- Dashboard, API and new CSP tests pass; the root repeats the synthetic browser connect/filter/disconnect flow.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_workload_content_security_policy.py tests/observability/test_api.py tests/observability/test_dashboard.py tests/observability/test_dashboard_workloads_ui.py -x -q`
- `python -m ruff check anvil_serving/observability/api.py tests/observability/test_workload_content_security_policy.py`

### T007: Declare global options explicitly in the command manifest

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** anvil_serving/commands/spec.py, docs/CLI-COMMAND-MANIFEST.json, tests/test_command_tree.py

The generated CLI index currently infers global options by intersecting every visible command's options. Sealed workload readers correctly omit topology-resolution globals, so this makes unrelated rows repeat all those flags. Export the canonical CommandTree.global_options as a top-level global_options array using the existing _option_data serializer, and increment the manifest schema from 6 to 7. Per-command options stay authoritative for supported flags; the new array is classification metadata, not inherited authority. Regenerate the manifest with write_manifest. No parser, dispatch, transport or workload policy change belongs here.

**Acceptance criteria:**

- Manifest global_options equals the canonical declared global options, including summaries and option metadata, with deterministic bytes.
- Schema version is 7; existing command records and the exact sealed workload/help options remain unchanged.
- Empty and custom global-option trees serialize their actual declaration instead of inferring it from commands.

**Verification:**

- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `python scripts/run_tests.py tests/test_command_tree.py -x -q`
- `python -m ruff check anvil_serving/commands/spec.py tests/test_command_tree.py`

### T008: Generate concise CLI rows from declared global metadata

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** scripts/audit_cli_references.py, tests/test_cli_reference_audit.py, tests/test_cli.py, .tickets/2026-09-05-workload-wire-contract-repairs.md
**Dependencies:** T007

Use the manifest's explicit global_options flag tuples in render_manifest_index, never an intersection or hardcoded flag list. Require a well-formed global_options array with the existing nonempty-string flag shape; malformed or missing metadata fails closed with a bounded ValueError before generation. Preserve command-local options even if every command happens to share them. No legacy-schema fallback is needed: the checked-in generator and schema ship together. Record the defect and candidate-only fix in the existing repair ticket. Synchronize the reproduced schema-6 assertion in tests/test_cli.py::test_command_manifest_is_terminal_and_machine_readable to schema 7 and check actual global_options output. Do not change CLI behavior. Generated artifacts are owned separately by T009; this task tests rendering and validation in memory and existing fixture scope.

**Acceptance criteria:**

- Adding a sealed command that omits resolution globals does not change unrelated rendered option rows.
- A shared non-global command flag remains visible; an empty global declaration hides nothing.
- Missing/malformed global metadata refuses; real workload rows retain exact explicit endpoint/query options and no resolution globals.
- Actual terminal CLI manifest emits schema 7 with explicit globals; no CLI behavior changes.

**Verification:**

- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `python scripts/run_tests.py tests/test_cli.py -x -q`
- `python -m ruff check scripts/audit_cli_references.py tests/test_cli_reference_audit.py tests/test_cli.py`
- `git diff --check`

### T009: Regenerate explicit-global references and current inventories

**Feature:** F001
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI.md, docs/CLI-REFERENCE-AUDIT.json, tests/fixtures/cli_reference_audit/expected.json
**Dependencies:** T008

After T008 is integrated, run the existing audit_cli_references.py --update --scope full. Keep generated blocks and inventories generator-owned. The full inventory must include newly tracked receiver packaging files; the fixture inventory remains unchanged when its inputs do. Do not infer that unchanged command syntax implies unchanged tracked file counts. Retain exact explicit workload options and concise unrelated rows.

**Acceptance criteria:**

- Generated command references and full inventory match the current indexed checkpoint.
- Fixture inventory changes only when its tracked inputs change.
- Full and fixture checks plus documentation invocation regressions pass.

**Verification:**

- `python scripts/audit_cli_references.py --check --scope full`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
- `git diff --check`

