# Prevent local path names from changing imported quantization

**Observed:** 2026-08-01

## Problem

The RTX PRO 6000 external-benchmark adapter inferred quantization from the
entire local source path. Running the suite from a worktree whose directory
contained `nvfp4` changed an AWQ fixture into NVFP4, so an exact comparison was
misclassified as a nearest-row match. The same artifact produced different
data depending on where it was downloaded.

## Resolution

Infer quantization only from the model identity and artifact filename. Keep
the full source path available for methodology metadata, but never let an
operator directory name alter model precision or quantization identity.

## Acceptance

- The AWQ fixture remains AWQ when its source path contains an `nvfp4` parent.
- The exact comparison test passes from this NVFP4-named worktree.
- Existing RTX PRO adapter and comparison tests remain green.
