# Workload wire-contract repairs

Date: 2026-09-05
Status: source repairs implemented; consolidated acceptance and deployment pending

## Absent query filters serialized as null

At integration faa2edc6, observability/probes/controller_workloads.py::_arguments
emits owner/kind/state/host with JSON null when absent. The canonical parser
requires omission instead. Direct reproduction:
`parse_node_workload_query(_arguments(WorkloadQuery()))` raises
`WorkloadError: query.owner must be a string`.
The T015 real-loopback parity test confirms REST and MCP succeed while the fleet
CLI returns fixed workload_source_unavailable; the underlying controller reply is
invalid_workload_query. Earlier mocked responses did not validate request arguments.

Repair only the shared serializer. Preserve explicit-null rejection and add
all optional subsets plus real node/fleet HTTP regressions. No credential or
transport fallback is permitted.

## Host bound differs from declared schema

The shared controller declaration advertises maxLength 64 and an ASCII identifier
pattern capped at 64. At the same revision,
`len(parse_node_workload_query({'host': 'n' * 65}).host)` returns 65.
Canonical _host and the dashboard currently accept up to 1024 characters.
The existing PRD explicitly requires declaration/parser parity.

Adopt the already-declared 64-character grammar in canonical host validation and
dashboard inputs/payloads. Do not shrink unrelated text fields or truncate hosts.

The implementation contract is docs/prds/workload-contract-repairs.md, tasks
T001-T005. Source fixes remain separate from final acceptance and deployment.

## Integrated regression fixtures

At 0c569b65 the historical dashboard test expected three buttons, before the
workload tab and its isolated connect/disconnect controls existed. T004 checks
the exact allowed labels and retains lifecycle mutation exclusions. At
0a2e0c69 the HTTP service credential-isolation fixture still expected null
filters. T005 updates that literal wire expectation and independently parses
the actual arguments while preserving the forbidden ambient-environment spy.

## Explicit CLI manifest global classification

At the T008 base, the CLI reference renderer inferred global options by
intersecting the flag tuples of every visible command. Sealed router and fleet
workload commands intentionally omit resolution globals, so that inference
repeated global flags on unrelated command rows. Conversely, a command-local
flag shared by all visible commands could be hidden as though it were global.

T007 publishes schema 7 with an explicit top-level `global_options` array.
The T008 candidate validates that declaration and excludes only its exact flag
tuples. Missing or malformed metadata fails closed; there is no legacy
intersection fallback. T009 owns regeneration of CLI.md and reference
inventories. This remains candidate source pending consolidated acceptance and
deployment.

## Source evidence

- T001: 1005be17, EV9C6C5074; 66 controller-source tests and Ruff passed.
  Reintroducing the old serializer failed 19 query cases.
- T002: 5c5db33f, EVB07E85C1; 104 canonical/collection tests and Ruff passed.
  The old host regex admitted a forbidden 65-character identifier.
- T003: 47b37e89, EVC64E5EFC; 11 dashboard tests and diff check passed.
  Removing the JavaScript bound fails on an invalid dispatched request.
- T004: e7996138, EV4C0EB22A; 13 dashboard tests and Ruff passed.
- T005: the combined service/transport gate is
  `python scripts/run_tests.py tests/observability/test_workload_http.py tests/observability/test_controller_workload_source.py -x -q`.
  Claim-bound results are recorded after commit; this ticket is not evidence
  of a full-suite, CI, formal acceptance, or live deployment pass.

