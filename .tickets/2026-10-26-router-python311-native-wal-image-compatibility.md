# Canonical router image compatibility with native WAL on Python 3.11

Status: open; source-image investigation and a reproducible startup check remain outstanding.

## Observed

During the 2026-10-26 Flash Next campaign restoration, an attempt to use the canonical router image failed at the Python 3.11/native-WAL runtime guard before service activation. Recovery used a reviewed incumbent-runtime overlay containing the PR #704 whitespace fix (image ID suffix `DE213`). The router configuration and GLM incumbent were restored and passed the retained local checks, but this overlay is not the original router image and does not fix the canonical build path.

The campaign evidence records the failed image attempt and the final image difference in the [dated finding](../docs/findings/2026-10-26-flash-next-engine-qualification.md) and [restoration evidence](../docs/findings/2026-10-26-flash-next-engine-qualification-evidence/final-restoration-evidence.json). The exact low-level cause of the guard failure is not established here.

## Follow-up

Inspect the canonical image build and startup path offline to identify why its Python 3.11 runtime fails the native-WAL guard. Add a bounded test that exercises the same native-WAL initialization and read/write path under the canonical image before activation.

## Acceptance

- The canonical image starts under the supported Python 3.11 environment without the incumbent-runtime overlay.
- A regression check verifies native-WAL initialization and a bounded read/write operation before the router is considered ready.
- The exact image digest and test result are retained. Do not treat the PR #704 overlay recovery as proof that the canonical image is fixed.
