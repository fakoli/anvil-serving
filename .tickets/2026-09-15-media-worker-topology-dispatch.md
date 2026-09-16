# Repair media worker topology dispatch for a declared managed runtime

**Observed:** 2026-09-15

## Problem

An explicit `serves status` request for a valid declared Anvil Media
topology/runtime refused to dispatch because `model-serve` reported zero
declared owners. The known local manifest command can operate the same worker
while retaining the GPU reservation gates. The two product paths therefore
disagree about valid managed media ownership.

## Boundary

This is a controller/dispatch resolution defect. It is not evidence that the
worker, GPU reservation, image workflow, or video workflow is invalid. No
direct-alias configuration, serve promotion, or fallback behavior may be added
to resolve it.

## Acceptance

- A declared managed media topology/runtime resolves through explicit `serves`
  status and lifecycle dispatch without a zero-owner rejection.
- Reservation checks remain active and use the existing declared owner.
- Regression coverage distinguishes a genuinely undeclared runtime from the
  valid media worker topology.
- The fix does not change media workflow availability or any route/alias.
