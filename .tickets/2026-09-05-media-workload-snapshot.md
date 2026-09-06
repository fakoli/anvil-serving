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
