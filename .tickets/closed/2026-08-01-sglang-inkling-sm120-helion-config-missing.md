# Inkling ships no SM120 Helion activation configuration

Status: fixed locally

## Symptom

After the SM120 grouped-GEMM staging fallback allowed the full checkpoint and
memory pools to initialize, decode CUDA-graph warmup failed in Inkling's
SiLU/multiply activation:

`RuntimeError: Helion kernel not tuned yet ... silu_and_mul_interleaved_sm_120.json`

The pinned SGLang image ships the Inkling Helion path but does not ship its
required SM120 configuration. The failure is deterministic after approximately
86-87 GiB of weights per rank and 3.14 GiB of BF16 KV/SWA pools are allocated.

## Decision

Do not run the full Helion creator while the model is resident: the tuner
materializes large primary and secondary tensors and only 3-4 GiB remains on
each card. Do not invent or reuse a tune from another GPU, engine revision,
dtype, TP size, or geometry.

SGLang already ships `silu_and_mul_triton()` in the same Inkling kernel module.
It uses a two-stage Triton schedule and is already called by Inkling's dense-MLP
path. Select it only when the runtime is SM120 and the activation layout is
interleaved; preserve the upstream Helion path everywhere else.

The final rebuilt image is pinned as
`anvil-sglang@sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`.

## Verification

The final pinned image completed decode CUDA-graph warmup with NCCL fallback,
became healthy, and passed independent functional, 12/12 capacity, and
repeated quality gates. The public finding records the fallback as a
compatibility workaround, not a tuned-kernel speedup. A dedicated offline
Helion tune and controlled A/B remain follow-up work and require the exact
engine, GPU product, model geometry, dtype, and TP=2 identity.
