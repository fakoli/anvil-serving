# Curated ComfyUI runtime must support Triton runtime compilation

**Observed:** 2026-08-28

## Problem

The exact FLUX.2 Klein qualification reached text encoding, then failed before
sampling. Managed worker logs showed PyTorch's Triton-backed native operation
attempting to compile its local CUDA driver module and refusing to continue
because no C compiler was installed in the curated image.

This is a runtime packaging defect, not model incompatibility or resource
exhaustion. Switching models or disabling the selected kernel path would hide
the missing dependency rather than make the managed runtime reproducible.

## Resolution

Install `gcc` and `libc6-dev` explicitly in the pinned derived image alongside
the existing runtime libraries. The base uses `--no-install-recommends`, so
installing `gcc` alone does not bring in the C library headers. Keep the CUDA,
PyTorch, ComfyUI, custom-node, graph, and model pins unchanged. Make the managed
compose command include `--build` so a reviewed Dockerfile change is evaluated
even when the old image tag already exists, then repeat the same managed
qualification.

## Verification

- Focused ComfyUI packaging tests passed.
- Managed `serves up` rebuilt the image and reached HTTP 200 readiness.
- The unchanged FLUX.2 Klein qualification completed with a decodable PNG.
- The full repository gate remains part of the enclosing change.
