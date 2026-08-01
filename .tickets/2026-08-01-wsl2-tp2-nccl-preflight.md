# Add a managed WSL2 TP2 NCCL preflight before model loading

**Observed:** 2026-08-01

## Problem

The first dual-GPU recipe attempt did not fail in model loading; it failed during
NCCL communicator initialization. Debug evidence showed two independent platform
boundaries on Docker Desktop/WSL2:

1. forced PCIe P2P produced an unhandled CUDA error; and
2. after P2P was disabled, NCCL 2.30.5 failed on both ranks at
   `ncclCuMemMapAndSetAccess` with CUDA error 999.

Each diagnosis currently requires starting a 100+ GB model server even though the
failure occurs before any checkpoint weight is loaded.

## Proposed resolution

Add a managed `host gpus` or recipe-preflight check that initializes a two-rank NCCL
communicator in the selected pinned runtime image. It should test the exact GPU pair
and report the transport/allocation controls needed on WSL2 without downloading or
loading model weights.

## Acceptance

- The check runs through an Anvil Serving CLI verb and never requires raw Docker as
  the operator path.
- Evidence includes host/runtime identity, selected devices, NCCL/CUDA versions,
  rank-to-device mapping, transport, cuMem mode, and a bounded all-reduce result.
- WSL2 failures recommend scoped recipe controls such as P2P and cuMem disablement;
  they never mutate global NCCL configuration.
- The command exits before model loading and provides a reusable JSON artifact for
  qualification findings.
