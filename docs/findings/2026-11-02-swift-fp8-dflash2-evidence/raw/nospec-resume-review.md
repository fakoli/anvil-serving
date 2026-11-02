# Independent no-spec resume review

Reviewed 2026-11-02, read-only. Inputs: the frozen original plan and runner,
the no-spec 8K/C1 native artifact and telemetry summary, its adjudication,
and the original arm queue. No request, lifecycle, or source action was
performed.

## Verdict: approve one fail-closed suffix resume, not a retry

The first no-spec 8K/C1 request population is not a model/protocol failure:
the native artifact reports 3/3 completed, and the retained after-metrics
counter deltas match 22116 prompt tokens, 329 output tokens, and three HTTP
responses. But the cell summary has `coverage_passed: false` because its
pre-cell metrics lacked the streaming counter series. The runner correctly
refused to infer a zero baseline; therefore exact counter reconciliation is
unavailable even though after-state counters happen to agree. Its speed metrics
must remain excluded.

The adjudication preserves that distinction, leaves raw artifact and telemetry
unchanged, and records no OOM/resource failure. Re-running it to obtain a
better baseline would be a prohibited retry-to-qualify. Continuing with the
five unrun rows is reasonable because a streaming series now exists and each
`run_cell()` invocation independently reloads/seal-checks the original plan,
product source, collector, exact activation/GPU0 selection, resource floors,
identity, no-overwrite paths, in-flight telemetry, drain, counter delta, and
OOM state.

## Required resume wrapper contract

The proposed private entrypoint is acceptable only if it fails before any
child process unless all of the following are true:

1. `load_plan()` succeeds and the full original no-spec queue exactly equals
   the frozen ordered six-row arm, not merely a set or a user-supplied subset.
2. The requested suffix is exactly rows 2–6 of that queue in order; it contains
   neither the excluded first row nor any dflash row.
3. The first row's native artifact and telemetry summary both exist, their
   SHA-256 values are recorded in a new invocation receipt, their plan/runner/
   collector seals equal the original frozen seals, and the summary records
   `coverage_passed: false` with incomplete counter reconciliation.
4. The adjudication exists, is SHA-bound in that receipt, identifies the same
   first row, and explicitly says its speed is excluded. The wrapper must not
   reinterpret or amend it.
5. Every suffix output directory and native artifact is absent before dispatch;
   use unchanged `run_cell()` for each row, stop at the first nonzero return,
   and write a new receipt recording the fresh identity receipt hash, ordered
   suffix, source/seal hashes, start/end, and per-row exit status.

The wrapper must not synthesize a cell summary, change the old plan/runner or
collector, modify the counter validator, dispatch a replacement 8K/C1 cell,
or pool its retained speed metrics into any aggregate. Final reporting should
state "no-spec 8K/C1 unqualified due to missing streaming baseline" and only
compare cells with their own `coverage_passed: true` evidence.

This is the smallest valid path. Altering the original runner's complete-arm
queue guard or adding an unsealed arbitrary-subset option would be broader and
weaker than a one-off receipt-bound suffix orchestrator.
