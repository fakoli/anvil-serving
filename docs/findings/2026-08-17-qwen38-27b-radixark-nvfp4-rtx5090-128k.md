# Qwen3.8 27B RadixArk NVFP4 128K qualification on RTX 5090

**Date:** 2026-08-17
**Decision:** retain direct 128K `challenger`; `no-promotion`
**Evidence:** `functional`, bounded deterministic `quality`, multimodal boundary
**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120
**Topology:** isolated Windows 11 / Docker Desktop / WSL2 qualification lane;
no RTX PRO 6000 was measured, protected, or involved

<!-- benchmark-result-card/v1 -->
## Result card

> RadixArk Qwen3.8 27B NVFP4 retained its direct RTX 5090 challenger status at
> 131,072 context after passing a 119,675-token retrieval request, the 30-case
> multimodal corpus, and the eight-image/two-video boundary corpus at c1.

| Setup | Qualified value |
|---|---|
| Model | `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`, served as `qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm` |
| Hardware | one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120; isolated Windows 11 / Docker Desktop / WSL2 lane |
| Runtime | SGLang `c4271c3fe1262fc2adbd162c33b25de5255251c5`; image `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`; ModelOpt NVFP4/FP8 weights, FP8 E4M3 KV, MTP disabled |
| Recipe | [managed 128K multimodal recipe at `85b21147`](https://github.com/fakoli/anvil-serving/blob/85b2114745a1a66f30252876ab528d49f12e8a73/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml) |
| Measurement path | direct online managed endpoint; retained artifacts do not classify individual requests as cold or warm |
| Contract | 131,072 context, 131,066 maximum input, c1, 512-token multimodal output cap, eight images or two videos, thinking disabled |
| Evidence | `functional`, bounded deterministic `quality`, multimodal boundary; retained qualification complete |
| Decision | retain direct 128K `challenger`, `no-promotion`; no route or deployment state changed; retain 64K rollback |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Long-context retrieval | pass in 29.811 s | 119,675 prompt plus 14 completion tokens, exact marker, `stop`; c1 |
| Established multimodal corpus | 30/30 | image 12/12, mixed 4/4, video 14/14; c1 |
| Media-count boundary corpus | 4/4 | two eight-image and two two-video attempts; c1 |
| Direct modality latency p50 | 0.882 / 1.535 / 1.602 s | image / mixed / video across the 30-attempt corpus |
| Retained invalid-expectation artifact | 2/4 | both video attempts passed; both image rubrics named absent source labels |

**Why it matters:** The exact managed 128K recipe preserves direct local
long-context and multimodal coverage while recording a concrete media-count
boundary and an exact 64K rollback.

**Important caveat:** This remains direct, concurrency-one evidence with no
controlled decode-rate benchmark or routed acceptance; the first image-boundary
artifact failed because its rubric required labels absent from the source
images and is retained alongside the corrected 4/4 run.

[Evidence manifest](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/README.md)
· [Publication summary](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/publication-summary.md)

## Outcome

The separate 128K managed recipe passed its retention gates and replaced the
64K candidate on the direct qualification endpoint. The server advertises a
131,072-token context window, a 131,066-token maximum input, a 167,789-token
KV pool, and explicit per-request ceilings of eight images and two videos.
No router alias or deployment promotion changed; the 64K recipe remains the
exact rollback.

The model returned the retrieval marker with 119,675 actual prompt tokens,
completed tools 20/20, passed direct image/OCR/video preflight, passed the
complete established multimodal corpus 30/30, and passed the new count-boundary
corpus 4/4: two eight-image attempts and two two-video attempts.

## Immutable identity and recipe

- Model: `RadixArk/Qwen3.8-27B-NVFP4` at
  `554ebba9b5f1b79dc11246341960360e6ef05ef4`.
- Runtime: `lmsysorg/sglang:qwen38-27b` at image digest
  `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- SGLang source revision from the image label:
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Managed recipe:
  `configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml`.
- Served name:
  `qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm`.
- ModelOpt NVFP4/FP8 mixed weights; FP8 E4M3 KV; TP=1; concurrency one;
  FlashInfer attention; 2,048-token chunks; CPU multimodal feature transport;
  radix cache disabled; one Mamba/GDN state slot; MTP disabled; thinking
  disabled.
- Exact cached snapshot: 21,945,295,265 logical bytes, with zero incomplete
  files or broken links.

Startup reported 30.08 GB available before weights and the same 20.14 GB model
footprint as the 64K baseline. After startup, the host reported 3,928 MiB free.
The engine retained the measured 167,789-token KV pool while raising request
admission from 65,536 to 131,072 tokens.

## Gates and measurements

| Gate | Result |
|---|---|
| Coding and structured JSON | pass |
| Long-context retrieval | pass; 119,675 prompt tokens plus 14 completion tokens, exact marker, `stop`, 29.811 s |
| Tool calls | 20/20 valid, `tool_calls` finishes |
| Thinking contract | explicitly disabled; reasoning output forbidden and absent |
| Direct image / OCR / MP4 | all pass with required labels and `stop` |
| Established multimodal corpus | 30/30: image 12/12, mixed 4/4, video 14/14 |
| Eight-image boundary | 2/2; ordered evidence from all eight positions |
| Two-video boundary | 2/2; both clips' state transitions returned in supplied order |

The established corpus used concurrency one, a 512-token output cap, four-image
and one-video evidence ceilings, thinking disabled, and only `stop` accepted.
Image latency was 0.882 seconds p50 / 1.973 p95; mixed was 1.535 / 2.222;
video was 1.602 / 134.440. The long video p95 is dominated by two descriptive
real-world clips. The boundary corpus used recorded ceilings of eight images
and two videos: eight-image p50/p95 was 3.803/3.927 seconds and two-video was
3.119/3.138 seconds.

Raw, sanitized evidence:

- [functional, requested ~120K, and tools preflight](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/preflight-functional-requested-120k.json)
- [119,675-token retrieval preflight](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/preflight-needle-119675-tokens.json)
- [direct image/OCR/video preflight](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/preflight-multimodal.json)
- [30-request established multimodal corpus](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/multimodal-c1.json)
- [passing eight-image/two-video boundary corpus](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/count-boundaries-c1.json)
- [failed invalid-expectation boundary artifact](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/count-boundaries-invalid-expectations.json)
- [source registry](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k-evidence/source-registry.json)

## Failure record and caveats

1. The first count-boundary artifact passed the two-video attempts but failed
   both eight-image assertions. Inspection proved the corpus asked for labels
   absent from its hash-pinned source images (`Q4`, `submit`, and a blue
   circle). The model correctly described the actual Q1-Q3 chart, status/GPU
   panel, and three blue boxes. The corrected corpus uses the assets' canonical
   labels and a repeated ordered code to cover the first and eighth images; it
   passed 4/4 under a new corpus hash. Both artifacts are retained.
2. The benchmark harness previously hard-coded one video per case. The managed
   surface now accepts a fail-closed `--max-videos-per-request` value from one
   through sixteen and records it in plans and artifacts. This is evidence
   admission only; it does not modify the engine or router.
3. The preflight generator's requested `--needle-ctx 120000` produced 99,049
   actual prompt tokens. A separate larger bounded request produced 119,675
   actual prompt tokens and is the basis for the long-context claim.
4. SGLang still warns that absent FP8 KV scaling factors default to 1.0.
   Deterministic gates passed, but equivalence to unquantized KV is unproven.
5. The result remains concurrency-one, direct-endpoint evidence. It does not
   establish routed media admission, controlled decode rate, broad GUI
   grounding, or a closed-loop computer-action policy.

## Decision boundary

Retain the exact 128K recipe as the active direct RTX 5090 vision/video
qualification candidate and retain the 64K recipe as rollback. The proven
request boundary is eight images or two videos, not an unlimited media count.
Do not route or promote without a separate human gate, router admission limits,
client acceptance, GUI-grounding/action evaluation, and a decision on the FP8
KV scaling warning.
