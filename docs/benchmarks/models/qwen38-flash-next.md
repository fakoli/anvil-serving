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
- Served name: `qwen38-flash-next-radixark-nvfp4-tp2-262k`
- SGLang image: `sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`
- Engine revision: `d91c3682b0b429e4c70df63cd57f819588ce29b0`

The checkpoint is a 180B-total multimodal MoE conversion. The tested
quantization applies ModelOpt NVFP4 W4A4 to routed experts while attention,
GDN, QSA, shared experts, vision, and MTP remain BF16; FP8 PLE tables are
dequantized to BF16 at load. This dossier makes no lossless-quantization claim.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards under WSL2, assigned exclusively to one
TP=2 owner over PCIe without NVLink. Aggregate VRAM is 192 GB but is not
unified memory. The engine reported a 416,064-token KV pool.

## Qualified recipe

The [262K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-262k-recipe.toml)
pins the exact weights and image. It uses page size 64, extra-buffer GDN
scheduling, track interval 64, 4,096-token chunked prefill, one running
request, 0.80 static memory, no automatic truncation, no speculative decode,
and no CUDA graphs. The [32K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-32k-recipe.toml)
is the retained conservative first-load lane.

The pinned runtime requires two bounded SM120/WSL2 compatibility selections:
an NCCL fallback for the symmetric-memory logits gather and SGLang's
device-agnostic QSA sparse decoder because the FA4 CuTe path fails MLIR
compilation on this host. These are exact-revision, fail-closed recipe patches,
not generic SGLang guidance.

## Evidence by measurement class

- `functional`: direct and authenticated routed coding, JSON, 253,325-token
  retrieval, tools 20/20, streaming tools, tool-result continuation, and
  Responses passed.
- `capacity`: the native endpoint accepted 262,137 prompt tokens plus one
  output token. The client contract retains an 8,192-token output reserve.
- bounded `quality`: the thinking-disabled protocol-v3 suite passed
  intelligence 6/6, session 3/3, and tools 3/3.
- `performance`: 4K/c1 measured 214.612 ms median TTFT, 2.627 s median E2E,
  and 12.801 tok/s median decode. One 125,442-prompt-token request measured
  11.961 s TTFT and 12.544 tok/s decode.
- live acceptance: OpenClaw, Hermes, and Pi selected the Primary and completed
  without fallback after the client synchronizer and local credential drift
  were repaired.

See the [dated promotion record](../../findings/2026-08-26-qwen38-flash-next-promotion.md)
and [sanitized evidence](../../findings/2026-08-26-qwen38-flash-next-promotion-evidence/summary.json).

## Decision boundary

This is the current text Primary reference for the exact promoted operator
profile. The 253,952-plus-8,192 client envelope and concurrency-one admission
are part of the contract. The c2 queue diagnostic is not a c2 qualification.
Multimodal weights being present does not promote image, OCR, or video aliases.

The portable QSA decoder is correctness-qualified but slow. Retain the exact
failure and performance evidence when evaluating a future FA4, graph, PLE,
or speculative-decoding lane; none can inherit this promotion without its own
matched functional, quality, capacity, and client gates.

## Dated history

| Date | Event | Result |
|---|---|---|
| 2026-08-26 | 262K TP=2 qualification and human-authorized promotion | current text Primary; direct/routed/client gates pass; bounded SM120/WSL2 compatibility lane retained |
