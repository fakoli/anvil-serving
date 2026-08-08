# Voice CLI tests mutated Docker

## Problem

Two nominal voice CLI unit tests invoked managed audio `up` and `down` without
`--dry-run`. They changed real Docker state, raced with local operators, and
could fail from container-name conflicts or in-progress removal.

## Decision

- Exercise manifest validation and lifecycle planning with `--dry-run`.
- Keep lifecycle execution behavior covered through injected serve fakes.
- Reserve real Docker mutations for explicit live/integration gates.

## Verification

- The focused voice suite no longer starts or removes audio containers.
- Managed lifecycle ordering and return-code tests still exercise both phases.
