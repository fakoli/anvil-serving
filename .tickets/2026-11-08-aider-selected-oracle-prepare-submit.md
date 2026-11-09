# Repeated selected-oracle preparation blocks ordinary Aider submission

Status: open; observed 2026-11-08.

## Reproduction and impact

Prepare an ordinary Aider job with an explicit, hash-pinned oracle receipt,
then submit that job with the same ownership ID and run ID. Preparation
passes, but the ordinary worker repeats asset preparation and fails with
`worker_runtime` / `FileExistsError` before model requests. The first collision
is the already-created C++ starter task under the owned
`aider/preparation/starter/` tree, even when the selected scored task is Go.

The [Go validation finding](../docs/findings/2026-11-08-tensorfold-go-validation.md)
retains this setup failure separately from model grades. No request-event
stream was produced, the worker exited, and the observed engine completion
counter was unchanged across this failed setup interval.

## Root cause

The ordinary branch of `benchmarking/worker.py` always calls
`prepare_harness_assets`. With an explicit selected oracle,
`prepare_aider_environment` requalifies all starter/reference pairs via
`_oracle`, whose `_copy_task` uses `shutil.copytree` into deterministic
per-run destinations. A prior successful standalone preparation leaves those
destinations present. The second preparation has no verified reuse or fresh
workspace path, so copying fails before the adapter dispatches model traffic.

## Current bounded workaround

Keep the failed run and its preparation artifacts. Submit a new run ID and
let the ordinary worker perform its one preparation pass; do not standalone
prepare the same run path first. Keep the selected oracle receipt, image,
adapter, grading contract and budgets unchanged. This avoids the collision;
it does not fix repeated preparation.

## Acceptance

Add a focused regression for explicit-oracle standalone prepare followed by
ordinary submission with the same owner/run ID. Repeat preparation must either
validate and reuse the exact owned evidence or use a fresh contained workspace
without weakening source, image, oracle or ownership checks. Changed or
untrusted prepared bytes must fail closed. Preserve earlier evidence and
prove that the valid repeated path reaches the existing preflight/adapter seam
without duplicate model dispatch.
