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

The same defect reproduced during the DeepSeek V4 Flash 0731 NVFP4 DSpark TP2
qualification. The managed target loaded the model and reached KV-cache
initialization, then exited with code 1 because 128K required 7.37 GiB of KV cache
per rank while only 3.46 GiB was available. The exclusive-mode transaction removed
the container before a later `models recipes logs` command could inspect it. A
concurrent managed log follower retained the traceback, but that race is not an
acceptable unattended evidence path.

## Impact

- The recovery message points to a command guaranteed to fail after compensation.
- Unattended campaigns lose the earliest actionable startup error.
- Diagnosis requires a second launch outside the mode transaction, increasing GPU
  churn and campaign time.

## Proposed resolution

Add an explicit `serves mode enter --preserve-on-failure` diagnostic lane. On
failed entry, stop the exact target, retain it only after Docker proves it is in
a stopped terminal state, and restore the split stack. If the stop fails or a
restart policy revives the target, remove it before restoration so diagnostics
cannot keep either GPU reserved. A future unattended-default enhancement may
also capture bounded logs and container identity into the mode journal before
ordinary cleanup.

## Acceptance

- A hermetic failed-entry test proves the flag retains a stopped/exited target
  while the split stack is restored.
- A restart-policy or stop-failure test proves unsafe retention falls back to
  removal before restoration.
- The CLI rejects the flag for `status`, `preview`, and `leave`.
- The printed guidance names the managed `serves logs` command for the retained
  target.
