# Keep store workload projections read-only and payload-free

Status: open; workload-visibility:T003 design correction before implementation.

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
