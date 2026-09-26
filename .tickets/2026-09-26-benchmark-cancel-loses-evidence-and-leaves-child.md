# Benchmark cancellation deletes uncollected evidence and leaves a child running

Status: open; observed with Anvil Serving 1.3.0 at
`a7d04b72f03b7302816342703f1711311a6f3a3f` on Linux.

## Observed behavior

A detached five-task SWE qualification was canceled after two tasks exhausted
their call limits without submitting patches. Even if every remaining task
resolved, the candidate could no longer reach the predeclared four-task floor.
The managed cancel command returned `state=cancelled`, `worker_terminated=true`,
and `cleanup_deferred=false`. Its partial artifact contained `results={}`.

Cancellation removed `work/mini-output`, including the completed trajectories
and submitted patches, before they had been collected into native evidence.
An exact-run `minisweagent` child remained alive. The operator verified its
module and run identifier, then terminated only that process and its child.
The working trajectories could not subsequently be copied. Prior observations
and independent review excerpts survive, but they are not full raw evidence or
an official completed grading result.

## Source boundary

`cancel_benchmark_job()` in `anvil_serving/benchmarking/worker.py` signals the
identified worker PID and immediately sets `terminated=True`; it does not wait
for exit or prove that benchmark descendants stopped. Its cleanup callback
then removes the entire work directory. Merely serializing the current job
result before that cleanup does not retain suite outputs still in `work/`.

## Required behavior

- Preserve bounded, owned native partial outputs and an explicit completeness
  record before cleanup. If retention fails, keep the work directory and report
  the gap; never invent scores or complete trajectories.
- Stop and verify the exact owned process tree, without signaling another run
  or treating delivery of a signal as proof of termination.
- Make repeated cancellation safe and retain failure diagnostics.
- Add a regression with a completed case trajectory and a live child process.
  Assert that cancellation retains the trajectory and leaves no owned child.
- Keep the incomplete campaign result labeled incomplete; do not rerun failed
  model work solely to conceal the evidence loss.

The related unsupported `--dry-run` help is tracked separately in
[the help/parser ticket](2026-09-23-benchmark-submit-preview-help.md).
