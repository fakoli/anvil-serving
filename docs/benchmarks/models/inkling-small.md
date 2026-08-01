# Inkling Small

## Current status and review date

Qualified exclusive TP=2 candidate; `no-promotion`. Review date: 2026-08-01.

## Immutable identity

`thinkingmachines/Inkling-Small-NVFP4` revision
`b6a99534467840620d411e4cd4ad5819b2610d9c`, served as
`inkling-small-nvfp4-tp2`.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards on Fakoli Dark in exclusive TP=2 over
PCIe without NVLink. Every other inference workload was offline.

## Engine, quantization, KV, context, and concurrency recipe

The working lane uses SGLang revision `b7252cc6b` from pinned derived image
`sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`,
native ModelOpt NVFP4 weights, Marlin FP4/MoE, Triton attention, BF16 KV/SWA,
32,768 context, one admitted request, and speculative decode off. The
SM120/WSL2 compatibility changes are narrowly gated and are not a claimed
kernel tune.

## Evidence by measurement class

The exact revision has `functional`, `capacity`, and `quality` evidence. With
`reasoning_effort=low`, smoke, JSON, 30K retrieval, tools 20/20, repeated
intelligence 6/6, session 3/3, and tools 3/3 all passed. The low-reasoning 32K
capacity lane completed 12/12 with TTFO p50 2.79 seconds, first-visible TTFT
p50 4.63 seconds, effective prefill 7,844 tok/s, and combined
reasoning/visible decode 73.5 tok/s. A separate reasoning-off lane completed
12/12 with 2.84-second TTFT and 74.6 tok/s visible decode.

## Decision and promotion state

`no-promotion`. The campaign changed no production alias or router profile.

## Failures and gotchas

The core reasoning-off preflight passed, but the extended Responses subset
still emitted internal reasoning with `reasoning_effort=none` and therefore
failed the stricter forbidden-reasoning evidence policy. The final runtime
also required revision-aware ModelOpt lookup, a missing loader dependency, an
SM120 two-stage grouped-GEMM fallback, the existing Triton activation fallback
where no SM120 Helion configuration was shipped, and the WSL2 logits-only
symmetric-memory guard. These compatibility fixes do not prove a performance
improvement. No genuine NVFP8-labeled Inkling artifact was found; the tested
checkpoint is the publisher's NVFP4 release.

## Dated run history

- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [Raw startup compatibility chain](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-startup-compatibility.json)
