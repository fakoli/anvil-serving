# CLI audit inventory omitted a newly tracked regression file

**Status:** Resolved 2026-08-30

## Problem

The full CLI-reference audit was run while a new product-family regression file
was still untracked. Because the audit deliberately scopes itself through
`git ls-files`, the local check covered 834 files and passed; after commit, the
exact-head CI saw 835 files and rejected the stale checked-in inventory.

## Acceptance

- Regenerate the reference inventory after every intended file is in the Git
  index.
- Re-run the read-only full audit against the same tracked candidate.
- Require the exact pushed head's CI audit before merge.

## Resolution

The generated inventory is refreshed after staging the complete correction set,
then checked again through the full audit. The failed exact-head CI run remains
part of the release record; it is not treated as a passing gate.
