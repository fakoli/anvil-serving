# Portable TP recipe inspection required host GPU selection

Status: fixed locally

## Symptom

The container-relative `CUDA_VISIBLE_DEVICES=0,1` contract correctly required
two matching host GPUs during load, but the same validation also ran while
`models recipes show` reconstructed a portable command. Read-only inspection
therefore failed before an operator could even see the recipe.

## Fix

Allow a valid, unique numeric device declaration to reconstruct with Docker's
`--gpus all` only when no container is being loaded. A real load still fails
closed unless the operator supplies an exact host device set whose count
matches the container-relative indices. Regression tests cover both paths.
