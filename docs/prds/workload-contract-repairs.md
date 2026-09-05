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

### T014: Bind workload timestamps to ordering and receipt time

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/workloads.py, anvil_serving/observability/workload_collection.py, tests/observability/test_workload_review_regressions.py, .tickets/2026-09-05-workload-final-review.md

Consolidated review at 7ef089b6 reproduced impossible updated-after-source records and stacked future allowance across source/node/fleet clocks. Enforce created <= updated <= source in the shared canonical record and validate records against trusted receipt time before query selection. Preserve independent source failure isolation and exactly 30 seconds inclusive, 30 seconds plus one microsecond exclusive. Remote provenance grants no extra skew. NodeResult/FleetResult decoding must reject compounded descendant skew against the enclosing collection as well. No schema, query selection, authentication or lifecycle change.

**Acceptance criteria:**

- Impossible ordering and compounded future timestamps refuse with fixed errors; source composition preserves valid peers.
- Exact 30-second receipt boundary passes and one microsecond beyond fails, including nested node/fleet decoding.
- Independently failing regressions and focused source gates are recorded; no live deployment is claimed.

**Verification:**

- `python scripts/run_tests.py tests/observability tests/router/test_workloads.py tests/test_manifest_workloads.py tests/test_recipe_workload_projection.py tests/control_plane/test_workload_sources.py -x -q`
- `python -m ruff check anvil_serving/observability tests/observability`
- `git diff --check`

### T015: Reject noncanonical workload records in the dashboard

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/observability/dashboard/static/workloads.js, tests/observability/dashboard_workloads_ui.cjs
**Dependencies:** T014

Mirror canonical owner/kind/state/phase/outcome/authority/quality and exact derived-label relations in the browser decoder. Timestamp parsing rejects rollover dates and suffixes, preserves microsecond ordering, and compares every nested timestamp to enclosing fleet collection without additive skew. Keep textContent, credential closure, generation fencing and single-flight behavior. Replace impossible arbitrary-label/store-stale happy-path fixtures with valid canonical records; keep private-label negative cases. Include every owner, invalid semantic combinations, ordering/future boundary, valid managed stale and media phases. An in-memory removed semantic guard must make the executable regression fail.

**Acceptance criteria:**

- Dashboard refuses contradictory states, arbitrary labels and invalid timestamps without rendering their contents.
- Valid canonical owner/state combinations and managed stale/media phases render correctly.
- All existing polling, credentials, abort and generation tests remain effective, including a negative control with otherwise-valid late data.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard_workloads_ui.py tests/observability/test_dashboard.py -x -q`
- `git diff --check`

### T016: Require exact replica identity at all eligibility boundaries

**Feature:** F001
**Priority:** high
**Type:** bugfix
**Likely files:** anvil_serving/router/availability.py, anvil_serving/router/serve.py, anvil_serving/router/model_capacity.py, tests/router/test_transition_integration.py, tests/router/test_streaming_relay.py, .tickets/2026-09-05-replica-identity-review.md

Consolidated router review reproduced routing and identity_passed evidence for health-only, model-mismatched and internally inconsistent AvailabilityResult values. Introduce one non-I/O exact replica identity predicate in availability.py; require the actual result type, available is True, ready state, identity_passed reason and expected/observed names equal the tier model. Normalize inconsistent snapshots to fixed unavailable at RoutingBackend before acquire_member, reuse the predicate for readmission and capacity projection, and prevent decision evidence from inventing identity based solely on a member ID. Keep TierAdmission model-agnostic. Update replica-only readiness fixtures to reflect real exact-identity observations. Ticket the reproduction and source-only fix; no direct-tier behavior or lifecycle authority change.

**Acceptance criteria:**

- Health-only, mismatched, malformed and inconsistent results cause no backend dispatch and no identity_passed decision.
- A valid equivalent member remains usable while an invalid peer is excluded; a wholly invalid set fails closed without retries.
- Hot path, readmission and metadata projection reuse the exact predicate, preserving direct-tier compatibility and model-agnostic admission.
- Router and replica lifecycle gates plus a broken-guard negative control pass before consolidated acceptance.

**Verification:**

- `python scripts/run_tests.py tests/router/ tests/test_replica_lifecycle.py -x -q`
- `python -m ruff check anvil_serving/router tests/router tests/test_replica_lifecycle.py`
- `git diff --check`

### T013: Pin manifest fixture timestamps to the injected clock

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** tests/test_manifest_workloads.py, .tickets/2026-09-05-manifest-fixture-clock.md

Full source verification at f0a369b2 reproduced 33 manifest-workload failures after the real clock passed the fixtures' frozen 2026-09-05 23:00 UTC clock. The production future-mtime guard is correct. Add a test-only file helper that writes manifest text and pins its mtime with os.utime to a fixed instant before _clock; use it for the main _manifest helper and regular sibling fixtures that are observed against a frozen clock. Preserve intentionally future mtimes as explicit overrides, existing budget-file timestamps, native filesystem reads, drift tests and all runtime timestamp assertions. Do not move the fixed clock into the future, use wall-clock now, monkeypatch production stat/time checks or weaken MAX_FUTURE_SECONDS. Add a literal mtime assertion for the helper and a boundary matrix with real fixture mtimes at observed+30 seconds (allowed) and observed+31 seconds (quarantined), retaining a valid sibling. That matrix must fail if the production future check is removed, without changing production code.

**Acceptance criteria:**

- All manifest fixtures observed against frozen clocks have explicit file timestamps, including regular valid siblings.
- Existing runtime/future/drift tests retain their intended independent failure causes.
- Literal fixture-mtime and future-boundary tests pass with actual temporary files; production behavior is unchanged.

**Verification:**

- `python scripts/run_tests.py tests/test_manifest_workloads.py -x -q`
- `python -m ruff check tests/test_manifest_workloads.py`
- `git diff --check`

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

### T012: Repair rendered methodology links and reconcile CSP evidence

**Feature:** F001
**Priority:** low
**Type:** modify
**Likely files:** docs/benchmarks/methodology.md, .tickets/2026-09-05-dashboard-workload-csp.md, .tickets/2026-09-05-scheduler-methodology-links.md
**Dependencies:** T006

Rendered HTML at the replica documentation checkpoint shows literal [tier transition] because its Markdown destination is separated by a newline. Keep that label/destination contiguous and point campaign artifact set to the published repeatable-campaigns guide instead of the findings index. Do not change qualification thresholds, promotion authority or claim a live benchmark. Track the concrete rendered defect in the new ticket. Update the CSP ticket's stale running/pending wording: its postcommit candidate 781708d5 / EV321129F4 passed 27 focused tests and Ruff; twenty consecutive repeats of the four framing cases passed (80 cases), but the original WinError 10053 cause remains unproven and no masking patch was made. Formal consolidated acceptance and deployment remain pending.

**Acceptance criteria:**

- Rendered methodology contains working tier-transition and repeatable-campaign links, not a literal Markdown label.
- CSP record distinguishes completed source tests, unreproduced transport interruption and pending formal/deployment gates.
- No source runtime or qualification policy changes.

**Verification:**

- `python -m mkdocs build --strict`
- `python scripts/check_markdown_links.py --root .`
- `python -c "from pathlib import Path; h=Path('site/benchmarks/methodology/index.html').read_text(encoding='utf-8'); assert '../../cli/router/#tier-transitions' in h; assert '../repeatable-campaigns/' in h; assert '[tier transition]' not in h"`
- `git diff --check`

### T010: Declare the dashboard's actual startup options

**Feature:** F001
**Priority:** medium
**Type:** bugfix
**Likely files:** anvil_serving/commands/host.py, docs/CLI-COMMAND-MANIFEST.json, tests/test_command_tree.py, .tickets/2026-09-05-workload-wire-contract-repairs.md
**Dependencies:** T007

The workload guide regression reproduced missing dashboard startup options in the canonical manifest while dashboard.app.build_parser accepts them. Declare all six existing parser options on the dashboard serve node: --host (IP), --port (PORT), --auth-env (ENV), --workload-controller-url (URL), --workload-expected-node (NODE) and --workload-authorization-policy (PATH). Preserve their existing parser defaults, scope/auth requirements, local host/native runtime ownership, foreground/process behavior and global resolution options; this is metadata repair, not a parser or authority change. Match summaries to the actual parser and use existing CommandOption idioms. Regenerate the schema-7 manifest without changing its schema. Add a literal six-flag test and compare those leaf options to the real argparse parser's non-help options, with no server start, network or credential lookup. Record the mismatch and candidate-only repair in the existing ticket.

**Acceptance criteria:**

- Canonical and generated dashboard serve options match every current non-help parser option with value placeholders.
- No dashboard runtime behavior, authentication, ownership or remote authority changes.
- Manifest regeneration is deterministic and focused command/dashboard regressions pass.

**Verification:**

- `python -c "from anvil_serving.commands.spec import write_manifest; write_manifest()"`
- `python scripts/run_tests.py tests/test_command_tree.py tests/observability/test_dashboard.py -x -q`
- `python -m ruff check anvil_serving/commands/host.py tests/test_command_tree.py`
- `git diff --check`

### T011: Refresh dashboard command references after declaration repair

**Feature:** F001
**Priority:** medium
**Type:** modify
**Likely files:** docs/CLI.md, docs/CLI-REFERENCE-AUDIT.json, tests/fixtures/cli_reference_audit/expected.json
**Dependencies:** T009, T010

Run the existing full reference generator at the integrated tracked checkpoint after T010. Require all six dashboard startup flags in the generated row. Keep fixture inventories byte-identical unless their inputs changed; include currently tracked receiver source/test files in the full inventory. Do not edit generated blocks by hand or claim this checkpoint remains current after further source additions.

**Acceptance criteria:**

- Published command row and inventories match the current indexed checkpoint.
- Full audit and documentation invocation regressions pass.

**Verification:**

- `python scripts/audit_cli_references.py --check --scope full`
- `python scripts/run_tests.py tests/test_cli_reference_audit.py tests/test_docs_command_invocations.py -x -q`
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
