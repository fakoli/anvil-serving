# Exclusive promotion preflight and recovery are not executable together

## Severity

P1 operational safety.

## Observed behavior

Two independent defects were reproduced during a human-approved promotion of
an already-running exclusive TP=2 target:

1. A promotion gate that declares `reasoning_effort` is rendered with both
   `--reasoning-effort` and the normalized `--thinking-mode` default. The
   preflight CLI correctly refuses that mutually exclusive combination before
   making a model request.
2. After that gate failure, automatic rollback stops the exclusive target and
   calls ordinary `cmd_up` for another exclusive rollback serve. Exclusive
   admission correctly refuses the start because the recovery path did not
   transfer or re-enter exclusive ownership. The result is a stopped target
   and a failed automatic rollback.

The dry-run rendering also prints the same exclusive-admission denial while
returning success, so it does not predict the live recovery failure.

## Required behavior

- Promotion gate normalization must emit exactly one reasoning-control family.
  A gate with `reasoning_effort` must not also emit `--thinking-mode`.
- An exclusive-to-exclusive promotion rollback must use an explicit ownership
  transfer or guarded mode transaction; it must never call ordinary `cmd_up`
  into an exclusive-admission refusal after stopping the prior owner.
- Dry run must model the live ownership path and fail if the corresponding live
  transition cannot be executed.
- Add regression tests for a running exclusive target, a failed reasoning gate,
  automatic exclusive rollback, and the dry-run/live equivalence.

## Safety boundary

Do not weaken exclusive admission. Fix orchestration so recovery satisfies the
existing admission contract.
