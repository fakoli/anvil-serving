# Canonical router image compatibility with native WAL on Python 3.11

Status: implementation in progress; the canonical Dockerfile pins Python 3.13 and verifies an official SQLite source build. The router target's exact-head image build is the outstanding verification gate.

## Observed

During the 2026-10-26 Flash Next campaign restoration, an attempt to use the canonical router image failed at the Python 3.11/native-WAL runtime guard before service activation. Recovery used a reviewed incumbent-runtime overlay containing the PR #704 whitespace fix (image ID suffix `DE213`). The router configuration and GLM incumbent were restored and passed the retained local checks, but this overlay is not the original router image and does not fix the canonical build path.

The campaign evidence records the failed image attempt and the final image difference in the [dated finding](../docs/findings/2026-10-26-flash-next-engine-qualification.md) and [restoration evidence](../docs/findings/2026-10-26-flash-next-engine-qualification-evidence/final-restoration-evidence.json). The source-level incompatibility is confirmed: the canonical image selected Python 3.11, while `_wal_runtime()` requires `sqlite3.Connection.setconfig()` and `getconfig()`, APIs added in Python 3.12. Python 3.11 therefore cannot pass this guard regardless of its linked SQLite version. The failed image's exact linked SQLite version was not separately captured.

## Follow-up

Keep CLI support at Python 3.11+. The shared `runtime` stage serves router, dashboard, and controller targets, so pinning that base also moves those images to Python 3.13 and SQLite 3.54.1. Build SQLite from the checksum-verified official amalgamation and run the real offline WAL conversion plus a key-store read/write smoke as the non-root runtime user with networking disabled. A focused GitHub Actions build targets the canonical multistage router image under a single-worker, 2-CPU/2-GiB BuildKit step limit; the managed local image builder's single-stage restriction remains unchanged.

## Acceptance

- The canonical router image builds from pinned official runtime and SQLite inputs and verifies the selected versions.
- The image-build smoke verifies native-WAL conversion and a bounded key-store write/read before the non-root router image can be activated.
- The exact image digest and test result are retained. Do not treat the PR #704 overlay recovery as proof that the canonical image is fixed.
