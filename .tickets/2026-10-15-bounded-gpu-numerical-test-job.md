# Bounded GPU numerical test job

**Observed:** 2026-10-15

## Problem

Qualification has reviewed numerical test plans but lacked a managed one-GPU
container job that could prove its image, sources, containment, deadline, and
cleanup without exposing private test paths or model state in the public repo.

## Resolution

`anvil-serving host gpu-test` accepts a strict private contract, previews before
creation, pins the local Docker daemon, rechecks one idle non-display GPU, and
runs sequential tests in an exact-owned temporary container. It records source
hashes, command receipts, containment, and verified cleanup. This creates no
route, recipe, or promotion state.
