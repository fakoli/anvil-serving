# Preserve dispatcher confirmation for exclusive mode transitions

**Observed:** 2026-08-01

## Problem

`anvil-serving serves mode enter ... --confirm` passed the top-level mutation
gate, but the dispatcher correctly stripped `--confirm` before invoking the
leaf parser. `cmd_mode` then checked only its legacy leaf argument, refused the
transition, and instructed the operator to rerun the identical command. No
container mutation occurred, but the documented production path was unusable.

## Resolution

Accept either the direct leaf `confirm` argument or the dispatcher's scoped
confirmation authorization. The scope is thread-local and exists only for the
current guarded dispatch, so direct unconfirmed leaf calls continue to fail
closed.

## Acceptance

- A confirmed top-level `serves mode enter` reaches the transactional apply.
- Direct calls without a leaf confirmation or dispatcher scope still refuse.
- Preview and dry-run behavior is unchanged.
- The confirmation scope is restored after the dispatch completes.
