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

## 2026-08-21 reproduction

The same recovery defect reproduced during the human-approved Infernal
Invocation r18 1M promotion campaign: after a failed intermediate promotion
attempt, automatic rollback tried ordinary bring-up for the exclusive r33
target and was correctly denied because ownership had not been transferred.
The later r18 promotion itself completed successfully and did not require
rollback. The exact r33 profile remains recoverable through the managed
exclusive-mode transition, but automatic promotion rollback is still not
live-proven. This ticket remains open.

## 2026-09-02 reproduction

The recovery defect reproduced again during the no-promotion SGLang
GLM-5.3-Flash qualification. A managed exclusive-mode entry successfully
started the retained exclusive target, but router readmission failed because
the invoking process did not have the configured token environment variable.
The transaction then stopped the target and attempted to start the declared
exclusive rollback group through ordinary `cmd_up`. Exclusive admission
correctly refused that start because the recovery path had not re-entered or
transferred exclusive ownership.

The operator restored the exact retained target through a fresh managed
exclusive-mode transaction after loading only the required user-local token
into that subprocess. The retained container identity, served model, router
admission, exclusive owner, and both GPU assignments were verified. This
confirms that the service remained recoverable while independently confirming
that automatic rollback is still defective. No admission weakening or raw
Docker mutation was used; this ticket remains open.
