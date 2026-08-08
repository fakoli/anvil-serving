# Allow exclusive entry when the declared router is stopped

**Observed:** 2026-08-01

## Problem

The split stack had a live direct Omni serve while the declared `anvil-router`
container was exited. `serves mode enter` would still invoke router quiesce and
drain for Omni's declared tier, causing a transaction failure even though no
router admission plane existed. Split restore already skipped readmission in
this exact offline-router state, so entry and rollback were asymmetric.

## Resolution

When and only when the caller uses the default managed router boundary and the
declared router container is absent or stopped, treat quiesce/drain as not
applicable and state that explicitly. An explicit router URL or injected
transition remains authoritative and is never skipped.

## Acceptance

- Default exclusive entry can stop a declared competitor and start TP=2 while
  the managed router container is stopped.
- No router transition command runs in that state.
- Explicit transition seams still execute and preserve drain-before-stop.
- Rollback restores the declared split group without attempting readmission
  into a stopped router.
