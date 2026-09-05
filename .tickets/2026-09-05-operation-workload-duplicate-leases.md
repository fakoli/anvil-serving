# Contain malformed duplicate leases in workload snapshots

Status: open; workload-visibility:T003.2 fix-forward.

A synthetic malformed operation_leases table with two rows matching one
operation record made list_workloads raise WorkloadError for duplicate workload
IDs. The left join duplicated records; per-row validation could not detect the
cross-row collision, and final SourceResult validation escaped the source error
boundary. Treat duplicate matching leases as fixed invalid/unavailable metadata
without unbounded Python materialization, and contain final result validation.
Retain one bounded read snapshot and no schema repair or lifecycle mutation.

Complete the existing task's focused contention/deadline, coherent writer,
WAL read-only, lease-boundary, terminal malformed-lease and stale-prefix/fresh-
lease regressions. These are implementation requirements, not a new per-task
adversarial acceptance pass. Formal acceptance is deferred to the final batch.
