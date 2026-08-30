# Exact Docker-image cleanup needs a managed product surface

**Status:** Open

## Problem

Anvil Serving can remove an exact unreferenced model-cache snapshot, but it has
no managed command that audits and removes one exact Docker image ID or digest.
Operators therefore cannot satisfy a bounded image-cleanup request without
dropping to raw Docker or using a broad prune.

The 2026-08-30 GLM campaign intentionally retained the previous GLM image as
the one-week rollback. A second superseded runtime image was unattached, but it
was also retained because neither a broad prune nor an unrecorded raw removal
meets the product's lifecycle and evidence contract.

## Required behavior

1. Accept exactly one immutable image ID or digest and reject tags alone.
2. Audit running/stopped containers, declared recipes/manifests, rollback
   references, and dependent child images before mutation.
3. Support dry-run and explicit confirmation with the exact reclaim estimate.
4. Refuse broad prune semantics, ambiguous identities, active references, and
   rollback-protected images.
5. Verify the exact postcondition and report that recovery requires rebuilding
   or pulling the immutable image again.

## Acceptance

- Add hermetic positive coverage for one unattached unreferenced image.
- Add negative coverage for active containers, stopped references, declared
  recipe/rollback references, child images, ambiguous tags, and identity drift
  between inspection and removal.
- Document the command in the generated CLI reference and operator guide.
- Pass Ruff, focused lifecycle tests, the full suite, and strict docs.
- Run one bounded live dry-run and confirmed cleanup of a disposable exact
  image before relying on the surface for model-campaign cleanup.
