# FLARE-FIX-001 incremental review package

`flare-fix-001-incremental.patch` is the reviewable delta from the saved local baseline to the reviewed v2 source. It has no private paths and is not an upstream submission or standalone cleanup patch.

## Exact base and replay

Start at `47581ccac82660953a5b59cb04b2750d64edc97d`, apply `../baseline-source/pre-existing.patch`, and install `../baseline-source/scheduler_trace.rs` as `helios/src/server/scheduler_trace.rs`. Then apply `flare-fix-001-incremental.patch`. A fresh temporary replay applied this sequence and matched every reviewed-current file byte-for-byte.

## Scope

The delta adds the configurable row/page allocation split, defaulting to 25%, and the opt-in transfer-byte audit with its configuration, budget accounting, tests, and docs. It changes 12 repository files, with 1,022 additions and 257 deletions.

## Prerequisite dependency

The patch depends on the saved pre-existing local telemetry/instrumentation baseline; it is deliberately excluded from this delta. In particular, the existing `StoreStats` host-memory fields and their producers in `helios/src/offload/{composite,index,memory,mod}.rs` are consumed by the existing metrics/runtime wiring, and the unchanged scheduler-trace module and its server wiring remain present. This package does not claim those prior local changes are standalone cleanup or ready for contribution.

For future review, keep the prerequisite telemetry/scheduler-trace work as one separate unit. The retained v1/v2 snapshots now support the exact, review-only sequence in [`SPLIT-REVIEW.md`](SPLIT-REVIEW.md). The original combined incremental patch remains the authoritative replay artifact; the two split patches are not newly tested or proposed for submission.

## Review boundary

The byte audit proves transfer payload equality for its exercised scope. It does not resolve the failed sampled-output gate or provide the unimplemented teacher-forced/logit numerical oracle.

The completed three-pair experiment supports a workload-specific cache benefit:
the complete 52-request one-token reverse-replay populations match, and their
median first-generated-token latency falls by 95.038–96.065%. Row-pressure
events fall from 112 to zero over priming plus replay, while completed eligible
restores rise from 10 to 36 per measured replay. These are allocator diagnostics,
not an engine comparison or a qualified default recommendation. The numerical
gate still fails, and the third pair exceeds the protected regression limit
for the small working set and page-heavy lane. See the
[hash-linked causal assessment](../analysis/final-causal-assessment.json) and
[independent review](../review/final-causal-review.md).

Keep the existing 25% default. The startup option is the useful contribution
shape to discuss with maintainers; the tested 75% setting is an explicit
workload choice requiring further numerical and regression work. Show this
package and its limitations to the user before any upstream submission.

At the 2026-10-23 UTC freshness check, upstream `main` was `e5bd6a67`,
132 commits after the experiment base. The named upstream surfaces still have
the quarter-row allocation and no equivalent row-share/admission options.
Adjacent `PrefixStore::poll` failure handling and Orbit forwarding changed;
an eventual contribution must reconcile those contracts against then-current
upstream. The experiment remains pinned and has not been rebased. See the
[bounded official-source comparison](../review/upstream-freshness.md).

## Later test-only delta

After the v2 image and patches were frozen, `../lifetime-test-proposal/lifetime-test.patch` added one composed CPU Worker/scheduler cancellation test. Apply it after the v2 combined delta if reviewing this later coverage. Its SHA-256 is `43f14e55bd3724c87d9a04c847ee50d2412ed2df056b5826f52118faa4fde149`; focused execution with `server,runtime-package` passed in `../lifetime-test-execution/r2/execution.json` and was independently accepted in `../review/lifetime-fixture-review.md`. The original missing-feature compile failure remains retained. This test-only delta is not part of the frozen v2 Docker image, original combined patch or split-patch replay claim. It covers controlled CPU success/failure completion after shared-waiter cancellation, not real DMA overlap or model numerical fidelity.
