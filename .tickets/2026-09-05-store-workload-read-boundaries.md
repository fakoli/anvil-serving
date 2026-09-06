# Keep store workload projections read-only and payload-free

Status: source implementation and combined regressions complete; consolidated
acceptance and deployment pending.

The existing BenchmarkJobStore and OperationStore connection helpers create
directories/tables and enable WAL. Operation lookup also expires records.
Reusing these helpers for an observation-only query would mutate owner state
and could report an absent database as an empty healthy source. Existing
full-row decoders retain benchmark specs/logs and operation responses/errors,
which are outside workload visibility's metadata-only contract.

Native job run IDs, idempotency keys and valid request IDs can be caller-supplied;
they are not safe owner-generated workload identity. Use a bounded owner-local
row identity with an explicit lifetime boundary, not a payload or caller label.

Specify a read-only snapshot connection, bounded primitive projection, canonical
pre-limit filters/order, an incompleteness sentinel and a query deadline.
Malformed/future rows must not withhold healthy peers, while unavailable storage
must remain unavailable. Source observation time must remain the authoritative
row update or running-operation lease heartbeat, not collection time.

Tests must prove no database creation, expiry or recovery, coherent concurrent
snapshots, no private payload materialization, truthful omission/freshness and
correct filtering before bounds. No lifecycle, schema migration or live
controller mutation belongs to this ticket.

## Combined source checkpoint

Benchmark snapshots (8dd48439 and WAL contract proof 32f5ae54), operation
snapshots (c768e6aa), and read-only reader construction (7e9546ff) are integrated.
T003 records their combined regression evidence without recreating either
projection or changing lifecycle behavior.

The gate
`python scripts/run_tests.py tests/test_benchmark_jobs.py tests/control_plane/test_benchmark_jobs.py tests/observability/test_workloads.py -x -q`
passed 125 tests on the integrated candidate; scoped Ruff also passed. The same
commands are rerun after this evidence commit for claim-bound submission.

Coverage includes filter/order before caps, lease-backed freshness, unknown
omissions, malformed/future peer quarantine, row-reuse fencing, bounded scalar
inputs, lock/query deadlines, no writable-store helpers, and concurrent snapshots.
An existing SQLite WAL reader may create SQLite-owned coordination sidecars;
the contract forbids application/schema/content writes, not those SQLite
coordination effects. Missing databases remain missing.

These local tests do not claim a live controller deployment or consolidated
batch acceptance.
