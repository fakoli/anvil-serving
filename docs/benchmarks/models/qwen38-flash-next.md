# Qwen3.8 Flash Next

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** measured EXL3 alternative to the September 14 GLM
      text selection; separate NVFP4 multimodal promotion history.
    - **Selected or best-qualified configuration:** EXL3 4.05-bpw at 262,144
      configured tokens in the September scout. The August NVFP4/SGLang
      TP=2/C1 recipe has separate text, image, OCR, and video acceptance.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q cards; native
      Linux for EXL3, WSL2 for the historical NVFP4 lane.
    - **Evidence:** EXL3 scored 91/100 on one fixed MMLU-Pro sample, context
      9/9, agentic 21/30, SWE 4/5, and image/OCR 12/12.
    - **Decision:** EXL3 remains `no-promotion`; GLM no-spec was selected in
      that campaign. NVFP4's August rollback role is historical.
    - **Important limitation:** EXL3 strict120 has zero performance-eligible
      responses because leading line feeds violated the canary contract.
      Neither its timings nor a one-point quality gap establish superiority.
    - **Review dates:** retained evidence through 2026-09-22; dossier reviewed
      2026-09-22.

[Latest finding](../../findings/2026-09-22-model-shortlist-validation.md) ·
[raw evidence](../../findings/2026-09-22-model-shortlist-validation-evidence/README.md) ·
[configuration history](#engine-quantization-kv-context-and-concurrency-recipe).

### Review narrative

#### 2026-09-23 — pinned NVFP4 candidate did not pass strict capacity

The medium profile resolved 4/5 frozen SWE tasks and passed Oracle7 18/18, but its strict C4 repair population was 0/4, so it is performance-ineligible and unqualified. Separate default-xhigh diagnostics passed preflight 26/26, vision 4/4, and a 412,589-token retrieval; those results do not qualify medium. No matched no-MTP control or controlled speed result exists. [Evidence](../../findings/2026-09-23-glm-spark-qwen-next-qualification.md).

#### 2026-09-22 — NVFP4 TP2 with C4 configured admission; serial C1 replacement scout did not advance

The distinct RadixArk NVFP4 recipe used TP2 with C4 configured admission; its
direct and agentic checks were serial C1. It loaded on native Linux and passed
six direct preflight groups. Its native agentic results were 16/18 greedy and 15/18 with
official sampling. Those artifacts pass their internal 0.75 floor, but the
campaign requires 18/18 deterministic success, so this candidate did not
advance to context, capacity, performance, vision, deep-coding, or SWE work.
Both greedy misses violate exact tool-call count/arguments; positional fixture
drift then limits interpretation. The sampling replay had three misses. A
paired GLM 16/18 scout exposed a planning-scorer false negative, so these
artifacts cannot rank broad coding or show Qwen is worse. **Outcome:**
`no-promotion`; retain GLM while harness validity blocks heavy qualification.

The startup log retains a nonfatal custom-allreduce UUID-to-index parse warning
and fallback; readiness and direct preflight still passed, so it is a runtime
caveat rather than a clean-start claim.

#### 2026-09-13–14 — EXL3 alternative retains quality and formatting gaps

The 4.05-bpw EXL3 profile completed the fixed quality sample, long-context,
agentic, SWE, and image/OCR probes. GLM no-spec completed 30/30 agentic tasks
against Next's 21/30; both resolved 4/5 frozen SWE tasks. These are bounded
samples across different configurations, not a general model ranking.
All 120 Next capacity responses stopped, but none met the strict canary-prefix
contract. The direct-stream diagnostic found two leading line feeds, so this
is a retained client/harness formatting failure, not a proven model defect.
**Outcome:** no promotion or valid strict120 speed claim for this profile.

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
retained text/image/OCR/video rollback. The label describes the August decision, not a current rollback assignment.

## Immutable identity

### September EXL3 scout

- **Checkpoint:** `turboderp/Qwen3.8-Flash-Next-exl3` at
  `55a732e0c4c3d4614bc42b68493bb930d9b02c0a`, 4.05-bpw conversion.
- **Runtime image:**
  `sha256:00909876aea02112b75d775bfa50f6d1adac50a3a11918363e7b403d7340d9bc`.
- **Identity evidence:** [configuration pins](../../findings/2026-09-13-intelligence-context-scout-evidence/configuration-identity.json).


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

### September EXL3 scout

The 262,144-token EXL3 profile is distinct from the August NVFP4 recipe below.
Its [native context artifact](../../findings/2026-09-13-intelligence-context-scout-evidence/native/next/next405-context-native192k-262k-r1/artifact.json)
retains the executed controls. The managed recipe is operator-private; use the
[public reconstruction guide](../configurations.md) and the pinned evidence.
Do not copy KV, speculation, or multimodal settings from the NVFP4 lane and
label that a reproduction of the EXL3 scout.


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

### September EXL3 quality and strict capacity

- **Status:** `quality`, `functional`, bounded context `capacity`; strict
  performance population ineligible.
- **Measured:** MMLU-Pro 91/100, context 9/9 above 257K total reserved tokens,
  agentic 21/30, frozen SWE 4/5, image/OCR 12/12.
- **Limits:** one quality pass, five SWE cases, and zero performance-eligible
  strict120 requests. The older NVFP4 video/client evidence does not transfer.
- **Evidence:** [dated finding](../../findings/2026-09-13-intelligence-context-scout.md) and
  [native artifacts](../../findings/2026-09-13-intelligence-context-scout-evidence/README.md).


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

### September EXL3 challenger

The EXL3 scout remains `no-promotion` after the September 14 GLM selection.

### Historical NVFP4 rollback

This was the retained video-capable rollback during the August GLM evaluation. Its historical promoted contract remains 253,952-plus-8,192,
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
| 2026-09-22 | [RadixArk NVFP4 TP2, C4 configured admission, serial C1 replacement scout](../../findings/2026-09-22-model-shortlist-validation.md) | direct preflight 6/6; agentic 16/18 and 15/18 miss the campaign 18/18 gate; `no-promotion` |
| 2026-09-13–14 | [EXL3 quality, context, agentic, SWE, and image scout](../../findings/2026-09-13-intelligence-context-scout.md) | `no-promotion`; strict120 performance ineligible |
| 2026-08-26 | [Full multimodal corpus, context curve, and vision-route/client promotion](../../findings/2026-08-26-qwen38-flash-next-vision-promotion.md) | then-current text/image/OCR/video Primary; direct 30/30; live 57/60 strict; edges 8/8; 25/25 context requests |
| 2026-08-26 | [QSA-fast plus matched MTP3 qualification and fix-forward promotion](../../findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md) | then-current text Primary; 154.9 tok/s at 4K and 134.1 at 128K; direct/routed/client gates pass |
| 2026-08-26 | Portable-QSA 262K TP=2 qualification and initial promotion | superseded same day; retained correctness and failure baseline |
