# Mode restore requires readmit even when the router is intentionally offline

Status: fixed locally

## Symptom

The saved `pre-campaign` group restored Omni and passed its health gate, but
`serves mode leave` then invoked `router readmit` against an intentionally
exited `anvil-router`. With no live admission plane, the command failed first
on the absent router token and rolled the otherwise healthy split restore back
to Inkling.

## Root cause

`_restore_split_stack()` treated every declared `router_tier` as proof that a
live router transition was required. It did not reconcile that declaration
with the default router container's actual lifecycle state.

## Fix

For the default local router boundary only, inspect `anvil-router` after all
saved serves pass readiness. If that router is absent, exited, created, or
dead, report that there is no live admission plane and complete the restore
without `readmit`. An explicitly supplied router URL or injected transition
still requires successful readmission, as does a running/restarting/paused
default router.

## Verification

Unit coverage proves the offline-router skip. The live retry must restore
healthy Omni, leave Primary absent and the router exited, remove the TP=2
owner, and report split mode with no unresolved reservations.

Verified live: Omni returned HTTP 200, Primary and Inkling were absent,
`anvil-router` remained exited, `mode leave` exited 0, and independent mode
status reported split mode with no unresolved reservation.
