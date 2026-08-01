# SGLang Inkling ModelOpt path omits accelerate

Status: fixed locally

## Symptom

The exact official Inkling Small NVFP4 TP=2 recipe reached NCCL initialization
on both RTX PRO 6000 ranks, then the pinned SGLang image exited before weight
loading. Both schedulers reported:

`ImportError: accelerate is required for ModelOpt quantization. Please install it with: pip install accelerate`

The failure is deterministic with the exact cached model revision and occurs
before any model-fit or inference claim can be made.

## Required fix

Pin a minimal derived image from the qualified SGLang digest that installs the
compatible `accelerate` dependency. Record the Dockerfile and resulting image
digest in the campaign recipe, retain the base digest, and rerun the same
managed load. Do not install into a running container or carry an unrecorded
one-off command forward.

## Fix

`configs/runtime-patches/sglang/b90c0d76-inkling-accelerate/Dockerfile`
derives only from the exact SGLang base digest and installs
`accelerate==1.14.0`. The build verified the imported version and produced
The dependency-only build produced
`anvil-sglang@sha256:b0a32a63be5b5002ed43a15a23f1751ff493776e79f4ad5c7a067a5e0593da41`.
The final pinned build also carries the separately ticketed revision-aware
checkpoint detector and is
`anvil-sglang@sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`.

## Verification

The final managed recipe re-verified the exact offline snapshot, initialized
both TP ranks, loaded the native NVFP4 checkpoint, and became healthy. Core
functional gates passed at both `reasoning_effort=none` and `low`; the final
low-reasoning capacity lane completed 12/12 and repeated quality passed
intelligence 6/6, session 3/3, and tools 3/3. Later unrelated runtime defects
were preserved in separate tickets.
