# Workload wire-contract repairs

Date: 2026-09-05
Status: reproduced; implementation and consolidated acceptance pending

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
T001-T003. Source fixes remain separate from final acceptance and deployment.

