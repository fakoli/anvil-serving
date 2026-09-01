# Qwen3.8 Flash Next

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Immediate retained text/image/OCR/video rollback in the
      latest dated public decision; it is not claimed as a live route.
    - **Selected or best-qualified configuration:** RadixArk ModelOpt NVFP4 on
      SGLang QSA-fast, exclusive TP=2, BF16 KV, 262,144 served tokens,
      concurrency one, NEXTN `3/1/4`, four images or one video, and no PLE CPU
      offload.
    - **Measured hardware:** Two RTX PRO 6000 Blackwell Max-Q cards over PCIe
      without NVLink, assigned exclusively to one TP=2 owner.
    - **Evidence:** Functional, capacity, bounded quality, matched performance,
      a 15-case multimodal corpus, router edges, context curve, and real
      OpenClaw, Hermes, and Pi acceptance.
    - **Decision:** Retain as the immediate video-capable rollback during the
      dated GLM evaluation; restoration still requires the managed lifecycle
      and fresh identity, route, sync, and client checks.
    - **Important limitation:** The c2 queue diagnostic is not c2
      qualification; different media limits, KV dtype, runtime patch, offload
      policy, context, or speculation preset were not qualified.
    - **Review dates:** Retained evidence cutoff: 2026-08-30. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-08-26 — Portable-QSA correctness baseline

The portable 262K TP=2 recipe established the same-day correctness baseline
for the pinned RadixArk checkpoint. It passed the bounded service gates and was
superseded later that day by the hardware-specific QSA-fast lane. It remains a
slower historical control, not a transferable performance claim.

#### 2026-08-26 — QSA-fast MTP3 qualification

The hash-gated SM120 QSA-fast path and its matched no-speculation control
qualified the exact TP=2 runtime. NEXTN `3/1/4` improved matched decode 2.33x
at 4K and 1.93x at 128K without a bounded quality regression. The full-reserve
request passed at 253,703 actual prompt tokens with an 8,192-token output
request inside the native 262,144-token window.

#### 2026-08-26 — Multimodal and real-client acceptance

The hash-pinned 15-case corpus passed 30/30 direct. Routed and live repeats,
router edge checks, and fresh OpenClaw, Hermes, and Pi turns established the
dated text/image/OCR/video contract with fail-closed admission at four images
or one video.

#### 2026-08-30 — Retained rollback after the GLM evaluation began

The 2026-08-26 record had authorized this profile for `llm.primary`,
`vision.general`, `vision.ocr`, and `vision.video`. When the GLM one-week
evaluation began on 2026-08-30, Qwen3.8 Flash Next became the immediate
retained text/image/OCR/video rollback. The label describes the latest public
decision, not verified live state after the evidence cutoff.

## Immutable identity

### Checkpoint and runtime

- Checkpoint: `RadixArk/Qwen3.8-Flash-Next-NVFP4`
- Revision: `7b719225242aacd3dbd3f9407468c2ee9a9d2594`
- Served name: `qwen38-flash-next-radixark-nvfp4-sglang-qsa-fast-tp2-262k-mtp3`
- SGLang image: `sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`
- Engine revision: `d91c3682b0b429e4c70df63cd57f819588ce29b0`

### Quantization boundary

The checkpoint is a 180B-total multimodal MoE conversion. The tested
quantization applies ModelOpt NVFP4 W4A4 to routed experts while attention,
GDN, QSA, shared experts, vision, and MTP remain BF16; FP8 PLE tables are
dequantized to BF16 at load. This dossier makes no lossless-quantization claim.

## Tested hardware and topology

### Qualified exclusive TP=2 lane

Two RTX PRO 6000 Blackwell Max-Q cards under WSL2, assigned exclusively to one
TP=2 owner over PCIe without NVLink. Aggregate VRAM is 192 GB but is not
unified memory. The retained MTP3 startup reported 6.275 GiB of KV cache per TP
rank, 12.55 GiB aggregate, and a 516,032-token maximum server allocation; the
matched no-speculation arm reported 415,744 tokens.

Single-card execution, another accelerator product, and co-resident serving
were **not tested** for this retained configuration.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified QSA-fast MTP3 lane and matched control

The [retained MTP3 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml)
and [matched no-speculation control](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-nospec-recipe.toml)
pin the exact weights, image, SM120 QSA patch, TP=2, 262,144 context, BF16 KV,
one running request, 0.80 static memory, backends, graph cap, parsers, transport,
and no PLE CPU offload. Their only intended performance difference is SGLang
NEXTN `3/1/4`.

### Exact-runtime compatibility controls

Startup hash-gates the exact QSA source and patched result from SGLang PR #36556
and fails unless the required FlashInfer decode symbol imports. The recipe also
retains the qualified NCCL logits fallback, Triton attention/prefill,
FlashInfer linear-attention decode, BF16 recurrent state, and batch-one CUDA
graph capture. FP8 KV remains excluded. These are exact-revision SM120/WSL2
compatibility selections, not generic SGLang guidance. The [portable 262K
recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-tp2-262k-recipe.toml)
is retained as the slower same-day correctness baseline.

## Evidence by measurement class

### Functional, capacity, and bounded quality

- `functional`: direct coding, JSON, 128K retrieval, full-reserve request,
  tools 20/20, and exact routed identity/readiness/admission passed.
- `capacity`: the MTP3 arm passed 253,703 actual prompt tokens with an
  8,192-token output request inside the native 262,144-token window. The client
  contract remains 253,952 plus 8,192.
- bounded `quality`: the thinking-disabled protocol-v3 suite passed
  intelligence 6/6, session 3/3, and tools 3/3.

### Performance and context

- `performance`: QSA-fast MTP3 measured 0.15 s TTFT, 0.36 s E2E, and
  154.9 tok/s decode at 4K/c1; at 128K it measured 12.67 s TTFT, 9,664
  prefill tok/s, and 134.1 decode tok/s. MTP3 improved matched decode 2.33x at
  4K and 1.93x at 128K without a bounded quality regression.
- context curve: 25/25 c1 requests completed at six targets from 4K through
  254K. Median decode was 155.9 tok/s at 4K, 114.7 at 128K, and 112.9 at the
  254K target with 245,000 actual prompt tokens. These 512-output-request rows
  remain separate from the 8,192-token full-reserve capacity proof.

### Multimodal, routing, and clients

- multimodal: the hash-pinned 15-case corpus passed 30/30 direct. Isolated
  router repeats scored 27/30 and 30/30; live repeats scored 29/30 and 28/30,
  or 57/60 strict. Every routed miss was a correct observation that omitted
  one literal rubric word. Router admission/SSE/tool/error edges passed 8/8.
- live acceptance: fresh OpenClaw, Hermes, and Pi turns selected the Primary,
  returned the fresh marker without fallback, and Pi/OpenClaw reported the
  262,144-token catalog contract. Their explicit vision references remain
  `vision.general`; fresh image-path acceptance is retained with the vision
  promotion.

See the [QSA-fast MTP3 promotion record](../../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md)
and [vision-promotion record](../../findings/2026-08-26-qwen38-flash-next-vision-promotion.md),
plus their sanitized evidence bundles.

## Decision and promotion state

### Retained rollback

This is the immediate retained video-capable rollback during the one-week GLM
evaluation. Its historical promoted contract remains 253,952-plus-8,192,
concurrency-one admission, four images, one video, and thinking disabled. The
c2 queue diagnostic is not a c2 qualification. Restoring route metadata alone
does not make the service live; a rollback requires the managed serve
lifecycle plus fresh direct identity, route, sync, and real-client checks. A
different media limit, KV dtype, worker/cache policy, or concurrent-media shape
requires a fresh gate.

### Transfer boundary

The portable QSA decoder remains a correctness-qualified historical control.
The retained SM120 fast path and MTP3 result cannot transfer to another runtime,
patch, KV dtype, offload policy, context, or speculation preset without matched
functional, quality, capacity, and client gates.

## Failures and gotchas

### Runtime and compatibility

- The pinned symmetric-memory logits path failed on SM120/WSL2; the qualified
  recipe uses the exact-revision NCCL fallback.
- The pinned FA4 CuTe sparse decoder failed MLIR compilation; the qualified
  lane first used SGLang's device-agnostic QSA decoder, then moved to the
  exact hash-gated PR #36556 SM120 fast path.
- Three exact Inferact/vLLM recipes were empirically disqualified by default
  NCCL initialization, V2-runner UVA, and a pre-KV V1 compile-autotune OOM.
  These failures do not prove universal checkpoint or 262K infeasibility.

### Measurement and integration

- The first long-tool generator undershot 100K measured tokens and was rerun
  with a calibrated prompt. That failed attempt is not counted as a pass.
- Exact-context router admission, Responses bounded-thinking translation,
  Hermes catalog drift, and Pi provider seeding were fixed forward before the
  final client acceptance. These product/configuration fixes do not broaden
  the model qualification beyond the recorded c1 text/image/OCR/video
  contract.

## Dated run history

| Date | Event | Result |
|---|---|---|
| 2026-08-26 | [Full multimodal corpus, context curve, and vision-route/client promotion](../../findings/2026-08-26-qwen38-flash-next-vision-promotion.md) | then-current text/image/OCR/video Primary; direct 30/30; live 57/60 strict; edges 8/8; 25/25 context requests |
| 2026-08-26 | [QSA-fast plus matched MTP3 qualification and fix-forward promotion](../../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md) | then-current text Primary; 154.9 tok/s at 4K and 134.1 at 128K; direct/routed/client gates pass |
| 2026-08-26 | Portable-QSA 262K TP=2 qualification and initial promotion | superseded same day; retained correctness and failure baseline |
