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

## 2026-09-04 Qwen3.8 campaign evidence

The later Qwen3.8 campaign again separated model-cache cleanup, Docker build
cache reclamation, and Docker image removal:

- The managed exact-revision model-cache surface removed
  `brandonmusic/GLM-5.3-Flash-tr3-4bpw@5ab363...` and reported only 9,725 bytes
  reclaimed because shared blobs remained deduplicated. It then removed
  `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66...` and reported
  137,095,744,155 bytes reclaimed.
- Post-cleanup inventory verified that the protected exact Qwen revisions and
  the active-restoration GLM revisions remained present.
- The managed automatic Docker build-cache reclaimer ran after recipe loads;
  retained examples include 31.7 to 1.8 GB earlier in the campaign and 1.3 to
  1.1 GB after the latest no-speculation load. Build-cache reclamation is not
  Docker image deletion.
- Docker inspection found many unattached images, but the product still could
  not audit and remove one exact image digest. The campaign performed no raw
  Docker image deletion and no broad Docker prune; those images remained.

This is recovery and gap evidence, not acceptance evidence for the requested
surface. It confirms that the existing managed cache operations can reclaim
model blobs and build cache while leaving the exact-image-removal gap open.

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
