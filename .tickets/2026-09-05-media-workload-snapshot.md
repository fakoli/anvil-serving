# Add bounded media lifecycle workload visibility

Status: implemented candidate a9d7620a; consolidated acceptance pending.

Implements workload-visibility:T010 through MediaJobStore.list_workloads.
Only bounded owner identity, fixed state and lifecycle timestamps are selected;
no job hydration, event/artifact reads or writable connection helper is used.
A read-only transaction and existing owner lock produce one coherent snapshot.
A top-k heap retains at most 200 matching records and a shared one-second
deadline bounds lock, SQLite wait and scan. Deadline exhaustion is unavailable,
not idle. Ordering/filtering applies to the complete bounded-time scan, so old
rows cannot hide a newer match. Corrupt peers retain partial trustworthy results.

The 82-test focused gate includes fixed mappings, cancellation spellings,
approval phases, exact freshness/recent boundaries, privacy, concurrent writer
consistency and no database creation on absent read. Existing WAL coordination
sidecars remain SQLite-owned; no lifecycle or schema mutations occur on read.

## Deployment gap observed 2026-11-08

A deployed Windows media node using CLI 1.5.0 returned
`workload_source_unavailable` for an authenticated operator workload query while
its four media lifecycle tools remained reachable. Native queue visibility alone
did not establish whether durable jobs remained unfinished. A bounded,
read-only, count-only store inspection was needed before maintenance; no job
payloads, events, artifacts, credentials or private host identities are published
here.

The cause is not yet distinguished between deployment configuration and an older
wire contract. The existing [wire-contract repair record](2026-09-05-workload-wire-contract-repairs.md)
describes a previously fixed serializer defect with the same outer error; this
observation does not prove that defect recurred or that the store projection is
missing. Inspect the owning controller's underlying error and installed revision
before adding another queue API or changing credentials.

Remaining acceptance: the installed operator path must return bounded, fresh
durable-job state through `MediaJobStore.list_workloads`, distinguish an empty
result from unavailable/unauthorized/partial evidence, and agree with the native
queue under maintenance. Preserve existing authorization and read-only storage
semantics. A healthy lifecycle endpoint or low GPU use is not proof of idleness.
