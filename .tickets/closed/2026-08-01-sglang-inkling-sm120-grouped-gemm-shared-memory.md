# Inkling Triton grouped GEMM exceeds SM120 shared memory

Status: fixed locally

## Symptom

The exact Inkling Small NVFP4 checkpoint loaded across both TP ranks, occupying
roughly 90-93 GiB per RTX PRO 6000, but SGLang exited during warmup. Its Inkling
grouped-GEMM Triton kernel requested 110,592 bytes of shared memory on hardware
whose per-block limit is 101,376 bytes:

`triton.runtime.errors.OutOfResources: out of resource: shared memory, Required: 110592, Hardware limit: 101376.`

The failure occurred in `grouped_gemm_triton()` with the hard-coded prefill
schedule `BLOCK_SIZE_M=128`, `BLOCK_SIZE_N=256`, `BLOCK_SIZE_K=64`,
`num_warps=8`, `num_stages=3`.

## Research boundary

Current SGLang SM120 reports document the same 101,376-byte limit and recommend
reducing Triton staging or tile size. SGLang's other SM120-aware GEMM paths
already select fewer stages. This is a compatibility fallback, not evidence of
an optimized kernel.

## Required fix

Gate Inkling's grouped-GEMM schedules on SGLang's existing
`is_sm120_supported()` helper. Use two stages for both prefill and sparse-decode
schedules on SM120 while retaining the upstream stage counts on other
architectures. Rebuild the exact derived image and retain the original failure
as baseline evidence.

The rebuilt image is pinned as
`anvil-sglang@sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`.

## Verification

The final managed recipe loaded the exact pinned checkpoint, completed warmup
on both GPUs without shared-memory exhaustion, became healthy, and passed the
functional, 12/12 capacity, and repeated quality gates. The public finding
labels this as an SM120 compatibility workaround and makes no speedup claim.
