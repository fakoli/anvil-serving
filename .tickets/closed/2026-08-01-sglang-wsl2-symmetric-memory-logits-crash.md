# SGLang TP logits symmetric-memory rendezvous crashes under WSL2

Status: mitigated in the pinned DeepSeek V4 qualification image; upstream/runtime fix still needed

## Symptom

The pinned SGLang DeepSeek V4 TP2 server loads all 48 checkpoint shards,
initializes both TP ranks, completes FlashInfer autotuning, and starts its HTTP
server. The first logits pass then terminates TP rank 0 with `SIGFPE`:

```text
Fatal Python error: Floating point exception
Current thread ... torch/distributed/_symmetric_memory/__init__.py ... rendezvous
... triton_symm_mem_ag.py ... create_state
... logits_processor.py ... _get_logits
Subprocess scheduler_0 ... crashed with exit code -8
```

TP rank 1 independently reports `CUDA driver error: invalid device ordinal`
while attempting the same multimem setup, then emits secondary NCCL TCPStore
errors after rank 0 exits. The failure happens before any benchmark request.

## Root cause boundary

`LogitsProcessor` constructs `MultimemAllGatherer` whenever TP logits require an
all-gather. That path lazily calls PyTorch symmetric-memory `rendezvous()` and
has no server argument or environment switch to disable the optimization. On
this WSL2/Docker Desktop CUDA proxy, the native rendezvous terminates the
process instead of raising a catchable Python exception, so SGLang's intended
NCCL fallback cannot run.

The upstream SGLang main branch still unconditionally enables this gatherer as
of 2026-08-01. This is separate from `--enable-torch-symm-mem`, which controls a
different all-reduce backend and is already false here.

## Managed mitigation

Build the pinned derived image from
`configs/runtime-patches/sglang/984699c2-wsl2-no-multimem-logits/`. Its one-line
behavioral change constructs the existing gatherer with `enabled=False`, which
selects SGLang's existing `tensor_model_parallel_all_gather` NCCL fallback. The
base image digest, patch, and derived image digest must all remain recorded in
the managed serve recipe and benchmark finding.

## Durable follow-up

- Add an upstream SGLang server flag or capability probe that disables only the
  multimem logits gatherer before its first rendezvous.
- Convert native symmetric-memory setup failures into a safe fallback where the
  platform permits it.
- Keep a TP2 regression that produces tokens and proves both ranks survive the
  first logits all-gather.
- Remove the derived patch only after a pinned upstream image passes that gate
  on this exact WSL2/Docker/GPU topology.

## Inkling reuse

The later Inkling Small NVFP4 qualification reproduced the same signature in
the same `LogitsProcessor` path: decode CUDA-graph capture raised a fatal
floating-point exception in `torch.distributed._symmetric_memory.rendezvous`,
followed by `multimem all-gather disabled (CUDA driver error: invalid device
ordinal)` on the other rank. Its separately pinned derived image applies the
same logits-only guard and retains NCCL TP collectives.
