# GLM-5.3-Flash

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** selected published text, tools, image, and OCR
      reference profile; this dossier records the evidence decision and does
      not claim current live route or serve state.
    - **Selected or best-qualified configuration:** exact
      `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with DFlash2 fixed-K5,
      corrected xgrammar runtime, TP=2/DCP=2, 524,288 configured tokens,
      router c16, and up to 16 images.
    - **Measured hardware:** two NVIDIA RTX PRO 6000 Blackwell Max-Q cards in
      exclusive TP=2 over PCIe without NVLink under Windows 11, Docker
      Desktop, and WSL2.
    - **Evidence:** 28/28 direct functional observations, bounded quality,
      206,296-actual-token retrieval, measured C2 at a nominal 250K target,
      83.08 tok/s at 4K, and pooled 69.99 tok/s at 240K.
    - **Decision:** retain the corrected 524K K5 profile as `current` in the
      published comparison; the former 1M K5 profile is first same-model
      rollback and the 4,096-token scheduler-chunk arm remains rejected.
    - **Important limitation:** video is unsupported; DFlash2 is
      noncommercial without separate permission; router c16 is not proof of
      sixteen simultaneous full-window requests.
    - **Review dates:** retained evidence through 2026-08-31; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-08-29 — initial Cardillo/Purtell qualification

The `brandonmusic/GLM-5.3-Flash-tr3-4bpw` 262K/524K campaign remains retained
historical evidence and the 262K image remains the one-week rollback. Adaptive
K1-K5 plus ReplaySSM stays rejected for tool-call corruption. The unserved
0xSero 3.0-bpw release remains watch-only.

#### 2026-08-30 — 1M optimization

The DFlash2 fixed-K5 profile with 2,048-token batching became the selected
same-model configuration in the dated campaign. The matched K3 arm remained a
verified alternate, while the 4,096-token scheduler-chunk arm was rejected.

#### 2026-08-31 — xgrammar fix-forward at 524K

The fix-forward qualification selected the exact
`wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with the DFlash2 fixed-K5
draft and a corrected xgrammar runtime as the replacement default. The
published contract covers text, tools, image, and OCR at 524,288 configured
tokens, router concurrency 16, and up to 16 images. Video is unsupported. The
former 1M K5 profile remains the first rollback.

## Immutable identity

- Target: `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1`
- Target revision: `319d66a8b53092b491f698440ecea781e4ddd4e4`
- Draft: `incoai/GLM-5.3-Flash-DFlash2`
- Draft revision: `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`
- Runtime image:
  `sha256:4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939`
- Runtime source base: `vllm-project/vllm@487ecf187`, with the hash-gated
  xgrammar reasoning-end/speculative-validation patch under
  `configs/runtime-patches/vllm/487ecf187-xgrammar-spec-reasoning-end/`
- Runtime-reported vLLM: `0.1.dev20051+g487ecf187`
- Served identity:
  `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-524k-vision-xgfix`
- Quantization: EXL3/MCG K3 routed experts with native
  attention/shared/vision/MTP tensors; FP8 DS-MLA target KV; BF16 DFlash2
  draft KV

The target, draft, and runtime are third-party artifacts. The DFlash2 draft is
CC-BY-NC-ND-4.0, so the combined recipe is evaluation/noncommercial unless
separate permission is obtained.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards, 96 GB each, under Windows 11,
Docker Desktop, and WSL2. They are assigned exclusively to one TP=2/DCP=2
owner over PCIe without NVLink. Aggregate VRAM is 192 GB, not unified memory.
The selected profile uses the V2 runner, B12x sparse MLA attention,
2,048-token batching, maxseq16, prefix caching, the visual tower, and
524,288 configured context.

CUDA-IPC peer access is unavailable in this WSL2 topology. The qualified
translation uses PyNCCL and disables custom PCIe all-reduce, B12X DCP A2A,
and top-k owner exchange while preserving EXL3 K3 compute, sparse B12X MLA,
FP8 target KV, and the DFlash2 draft.

## Engine, quantization, KV, context, and concurrency recipe

| Recipe | Role | Context | Decision |
|---|---|---:|---|
| [DFlash2 K5, corrected xgrammar](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) | selected text/image/OCR default | 524,288 | `verified`, `current` |
| [Matched no-speculation control](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) | reliability/performance control | 524,288 | `verified`, control |
| [Former DFlash2 K5, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | first rollback | 1,048,576 | historical `verified`, rollback |
| [DFlash2 K3, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-exl3-k3-dflash2-k3-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | former high-concurrency alternate | 1,048,576 | historical `verified` |
| K5, batch 4,096 | matched scheduler-chunk trial | 1,048,576 | `rejected`; no durable recipe |
| [TR3 vision fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | prior interactive profile and image rollback | 262,144 | historical `verified` |
| [TR3 no spec](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) | prior maximum-context/headroom lane | 524,288 | historical `verified` |

The corrected 524K engine reported 19.18 GiB of available KV memory per rank
and 2,493,817 KV tokens, or 4.76 complete configured windows. Router c16 is a
scheduling ceiling for short requests, not proof of sixteen simultaneous 524K
prompts. The measured long-context concurrency gate is C2 at a nominal 250K
target. The locally qualified media contract is up to 16 images and zero
videos.

## Evidence by measurement class

- `functional`: both matched arms passed 28/28 direct observations, including
  strict JSON, 206,296-actual-token retrieval, tools 20/20, a structured tool
  after the long prompt, streaming tools, tool-result continuation, and
  Responses. The selected arm also passed image understanding and OCR.
- `capacity`: C2 at a nominal 250K target / 206,630 actual prompt tokens per
  request completed 2/2 with an 8,192-token API completion allowance; the
  engine reported 2,493,817 KV tokens.
- bounded `quality`: intelligence 6/6, session 3/3, tools 3/3, and the 4K,
  131K, and 240K context cases passed with no retained failure.
- `performance`: the matched DFlash2 arm measured 83.08 tok/s at 4K and a
  pooled 69.99 tok/s across two five-request 240K runs, versus 42.61 and 43.63
  for no speculation. The two 240K run-level medians were 64.51 and 70.50.
- `concurrency`: the selected profile completed the measured 250K-class C2
  gate 2/2. Router c16 remains a short-request scheduling ceiling.
- multimodal: direct image understanding and verbatim OCR passed; up to 16
  images are admitted. Video remains disabled.
- client acceptance: real OpenClaw and Hermes shell-tool continuations passed
  with exact identity and no fallback. Pi 0.84.2's initial normal
  extension-loaded PTY path made exactly one `read` call, recovered an unseen
  marker, and emitted zero error events; the goal-closure recheck on Pi 0.84.4
  retained 524,288/8,192 and passed another real PTY tool nonce.

### Matched local comparison

| Requested context | Matched no spec | Corrected DFlash2 K5 | Former 1M K5 | Current vs no spec | Current vs former 1M |
|---:|---:|---:|---:|---:|---:|
| 4K | 42.61 tok/s | **83.08 tok/s** | 82.1 tok/s | **+95.0%** | +1.2% |
| 240K | 43.63 tok/s | **69.99 tok/s pooled** | 67.9 tok/s | **+60.4%** | +3.1% |

The 240K selected value pools ten requests across two retained runs; their
run-level p50 values were 64.51 and 70.50 tok/s. This comparison is local to
the exact derived runtime and dual-Max-Q/WSL2 topology; it is not a general
model-intelligence ranking.

## Decision and promotion state

The corrected 524K K5 profile with 2,048-token batching is the selected
text/image/OCR default. It retains measured C2 headroom at a 250K-class prompt,
eliminates the speculative structured-output failure, and improves local
decode over both the matched no-speculation control and the former 1M K5
profile. The former 1M image/config remains the first rollback. Raising
batching to 4,096 remains rejected from the preceding campaign.

The 524K route advertises 8,192 maximum output, router c16, and 16 images.
`llm.primary`, `llm.secondary`, `llm.auxiliary`, `llm.voice`,
`vision.general`, and `vision.ocr` select the same exact service during this
evaluation. Qwen3.8 Flash Next remains the retained video-capable rollback;
the former 1M GLM profile is the first same-model rollback and the earlier
262K GLM image remains an additional historical rollback.

## Failures and gotchas

- DFlash2's published license is noncommercial/no-derivatives. Obtain separate
  permission before commercial use.
- The target/draft/runtime are community artifacts, not stock-vLLM support.
- The original DFlash2 runtime failed structured generation after reasoning
  termination. Only the digest-pinned corrected xgrammar image is qualified.
- Video is unsupported; the DFlash2 drafter receives text-only inputs on image
  calls while the target processes the image.
- WSL2 peer IPC failed, requiring the qualified PyNCCL transport translation.
- The runtime suggested a larger fixed KV pool, but it was not A/B tested; the
  selected 0.95 utilization retains measured operating reserve.
- No missing MoE/GEMM tune warning appeared. First-request JIT warnings are
  warm-up observations, not evidence that a kernel tune would help.
- The high-control quality request was accepted but independent token-level
  reasoning telemetry was unavailable; the artifact says
  `requested_unverified`.
- The rollback profile needs its already-qualified visible/reasoning budget;
  an artificially small visible cap can end in reasoning without an answer.
- No exact Docker-image removal product surface exists. The previous GLM image
  remains the intended one-week rollback; no broad prune was used.

## Dated run history

| Date | Event | Result |
|---|---|---|
| 2026-08-31 | xgrammar fix-forward image, matched 524K no-spec/DFlash2 A/B, C2 250K-class gate, rollback drill, router, and real-client forward restore | corrected DFlash2 K5 selected at 524K; 83.08 tok/s at 4K and pooled 69.99 at 240K; C2 2/2; full [finding and raw artifacts](../../findings/2026-08-31-glm53-xgrammar-524k-qualification.md) |
| 2026-08-30 | Current-source refresh, K5/K3/batch4,096 A/B, 4K-500K performance, 950K retrieval, image/OCR, quality, router, and real-client promotion | K5/batch2,048 selected as `current` one-week default; K3 verified alternate; batch4,096 rejected; full [finding and raw artifacts](../../findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md) |
| 2026-08-29 | Cardillo/Purtell translation, adaptive/fixed/no-spec A/B, vision/OCR, 250K and near-500K capacity | TR3 vision fixed K5 and no-spec 524K qualified as challengers; adaptive MTP rejected; 0xSero 3.0-bpw watch-only; [historical finding](../../findings/2026-08-29-glm53-cardillo-purtell-qualification.md) |
