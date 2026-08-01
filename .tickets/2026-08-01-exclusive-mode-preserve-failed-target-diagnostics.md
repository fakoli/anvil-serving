# Preserve failed exclusive-target diagnostics before rollback removal

**Observed:** 2026-08-01

## Problem

When an exclusive-mode target's managed `up` command fails, `serves mode enter`
immediately removes the exited target container and restores the split stack. The
nested recipe loader prints a bounded managed-log command, but by the time the
operator can run it the mode transaction has already removed the exact container.

This was observed on the TP2 Qwen qualification after fail-fast readiness correctly
reported `state=exited`. The useful engine traceback existed in the container but
was discarded by the higher-level compensating cleanup.

## Impact

- The recovery message points to a command guaranteed to fail after compensation.
- Unattended campaigns lose the earliest actionable startup error.
- Diagnosis requires a second launch outside the mode transaction, increasing GPU
  churn and campaign time.

## Proposed resolution

Before removing a failed exactly owned target, capture bounded managed logs and
container identity into the mode transaction's evidence/journal. Keep rollback
automatic; retain diagnostics rather than retaining the failed workload.

## Acceptance

- A hermetic failed-entry test proves bounded logs are captured before `rm`.
- The record includes model/revision labels, image identity, exit state/code, and
  the final bounded log tail with credentials sanitized.
- Log-capture failure never blocks rollback and is recorded explicitly.
- The printed inspect guidance points to the retained artifact when the container
  has already been removed.
