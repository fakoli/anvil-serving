# Qwen3.8 Flash Next

## Current status and review date

The 2026-08-26 record qualifies and promotes the RadixArk ModelOpt NVFP4
checkpoint as the human-authorized text `llm.primary` reference on two RTX PRO
6000 Blackwell Max-Q cards at exclusive TP=2. The served window is 262,144
tokens with a client contract of 253,952 prompt tokens plus 8,192 output tokens
and concurrency one. Authenticated routing and real OpenClaw, Hermes, and Pi
acceptance passed. Review date: 2026-08-26.

## Immutable identity

- Checkpoint: `RadixArk/Qwen3.8-Flash-Next-NVFP4`
- Revision: `7b719225242aacd3dbd3f9407468c2ee9a9d2594`
- Served name: `qwen38-flash-next-radixark-nvfp4-sglang-qsa-fast-tp2-262k-mtp3`
- SGLang image: `sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`
- Engine revision: `d91c3682b0b429e4c70df63cd57f819588ce29b0`

The checkpoint is a 180B-total multimodal MoE conversion. The tested
quantization applies ModelOpt NVFP4 W4A4 to routed experts while attention,
GDN, QSA, shared experts, vision, and MTP remain BF16; FP8 PLE tables are
dequantized to BF16 at load. This dossier makes no lossless-quantization claim.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards under WSL2, assigned exclusively to one
TP=2 owner over PCIe without NVLink. Aggregate VRAM is 192 GB but is not
unified memory. The current MTP3 startup reported a 516,032-token maximum
server token allocation; the matched no-speculation arm reported 415,744.

## Engine, quantization, KV, context, and concurrency recipe

The [current MTP3 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml)
and [matched no-speculation control](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-nospec-recipe.toml)
pin the exact weights, image, SM120 QSA patch, TP=2, 262,144 context, BF16 KV,
one running request, 0.80 static memory, backends, graph cap, parsers, transport,
and no PLE CPU offload. Their only intended performance difference is SGLang
NEXTN `3/1/4`.

Startup hash-gates the exact QSA source and patched result from SGLang PR #36556
and fails unless the required FlashInfer decode symbol imports. The recipe also
retains the qualified NCCL logits fallback, Triton attention/prefill,
FlashInfer linear-attention decode, BF16 recurrent state, and batch-one CUDA
graph capture. FP8 KV remains excluded. These are exact-revision SM120/WSL2
compatibility selections, not generic SGLang guidance. The [portable 262K
recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-262k-recipe.toml)
is retained as the slower same-day correctness baseline.

## Evidence by measurement class

- `functional`: direct coding, JSON, 128K retrieval, full-reserve request,
  tools 20/20, and exact routed identity/readiness/admission passed.
- `capacity`: the MTP3 arm passed 253,703 actual prompt tokens with an
  8,192-token output request inside the native 262,144-token window. The client
  contract remains 253,952 plus 8,192.
- bounded `quality`: the thinking-disabled protocol-v3 suite passed
  intelligence 6/6, session 3/3, and tools 3/3.
- `performance`: QSA-fast MTP3 measured 0.15 s TTFT, 0.36 s E2E, and
  154.9 tok/s decode at 4K/c1; at 128K it measured 12.67 s TTFT, 9,664
  prefill tok/s, and 134.1 decode tok/s. MTP3 improved matched decode 2.33x at
  4K and 1.93x at 128K without a bounded quality regression.
- live acceptance: fresh OpenClaw, Hermes, and Pi turns selected the Primary,
  returned the fresh marker without fallback, and Pi/OpenClaw reported the
  262,144-token catalog contract.

See the [QSA-fast MTP3 promotion record](../../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md)
and [sanitized evidence](../../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-evidence/summary.json).

## Decision and promotion state

This is the current text Primary reference for the exact promoted operator
profile. The 253,952-plus-8,192 client envelope and concurrency-one admission
are part of the contract. The c2 queue diagnostic is not a c2 qualification.
Multimodal weights being present does not promote image, OCR, or video aliases.

The portable QSA decoder remains a correctness-qualified historical control.
The current SM120 fast path and MTP3 result cannot transfer to another runtime,
patch, KV dtype, offload policy, context, or speculation preset without matched
functional, quality, capacity, and client gates.

## Failures and gotchas

- The pinned symmetric-memory logits path failed on SM120/WSL2; the qualified
  recipe uses the exact-revision NCCL fallback.
- The pinned FA4 CuTe sparse decoder failed MLIR compilation; the qualified
  lane first used SGLang's device-agnostic QSA decoder, then moved to the
  exact hash-gated PR #36556 SM120 fast path.
- Three exact Inferact/vLLM recipes were empirically disqualified by default
  NCCL initialization, V2-runner UVA, and a pre-KV V1 compile-autotune OOM.
  These failures do not prove universal checkpoint or 262K infeasibility.
- The first long-tool generator undershot 100K measured tokens and was rerun
  with a calibrated prompt. That failed attempt is not counted as a pass.
- Exact-context router admission, Responses bounded-thinking translation,
  Hermes catalog drift, and Pi provider seeding were fixed forward before the
  final client acceptance. These product/configuration fixes do not broaden
  the model qualification beyond the recorded text Primary contract.

## Dated run history

| Date | Event | Result |
|---|---|---|
| 2026-08-26 | QSA-fast plus matched MTP3 qualification and fix-forward promotion | current text Primary; 154.9 tok/s at 4K and 134.1 at 128K; direct/routed/client gates pass |
| 2026-08-26 | Portable-QSA 262K TP=2 qualification and initial promotion | superseded same day; retained correctness and failure baseline |
