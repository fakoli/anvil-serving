# GLM-5.3-Flash

## Current status and review date

The 2026-08-30 dual-PRO optimization selected the exact
`wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with the DFlash2 fixed-K5
draft as the human-authorized one-week default. It owns the text, tools,
image, and OCR aliases at 1,048,576 configured tokens, router concurrency 16,
and up to 16 images. Video is unsupported. K3 is a verified high-concurrency
alternate; the 4,096-token scheduler-chunk arm is rejected. Review date:
2026-08-30.

The earlier `brandonmusic/GLM-5.3-Flash-tr3-4bpw` 262K/524K campaign remains
retained historical evidence and the 262K image remains the one-week rollback.
Adaptive K1-K5 plus ReplaySSM stays rejected for tool-call corruption. The
unserved 0xSero 3.0-bpw release remains watch-only.

## Immutable identity

- Target: `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1`
- Target revision: `319d66a8b53092b491f698440ecea781e4ddd4e4`
- Draft: `incoai/GLM-5.3-Flash-DFlash2`
- Draft revision: `dc77ff1c99eeb2df044ee3d4f0094eb033fee410`
- Runtime image:
  `sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75`
- Runtime source:
  `tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx@d46fdeddf8c6fec2d4595b65535a32d80a5af787`
- Runtime-reported vLLM: `0.1.dev20051+g487ecf187`
- Served identity: `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-1m-vision`
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
2,048-token batching, maxseq16, prefix caching, and the visual tower.

CUDA-IPC peer access is unavailable in this WSL2 topology. The qualified
translation uses PyNCCL and disables custom PCIe all-reduce, B12X DCP A2A,
and top-k owner exchange while preserving EXL3 K3 compute, sparse B12X MLA,
FP8 target KV, and the DFlash2 draft.

## Engine, quantization, KV, context, and concurrency recipes

| Recipe | Role | Context | Decision |
|---|---|---:|---|
| [DFlash2 K5, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | selected text/image/OCR default | 1,048,576 | `verified`, `current` |
| [DFlash2 K3, batch 2,048](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-exl3-k3-dflash2-k3-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) | high-concurrency alternate | 1,048,576 | `verified`, alternate |
| K5, batch 4,096 | matched scheduler-chunk trial | 1,048,576 | `rejected`; no durable recipe |
| [TR3 vision fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | prior interactive profile and image rollback | 262,144 | historical `verified` |
| [TR3 no spec](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) | prior maximum-context/headroom lane | 524,288 | historical `verified` |

The engine reported 16.61 GiB of KV allocation per card and 2,917,371 KV
tokens, or 2.78 complete configured windows. Router c16 is a scheduling ceiling
for short requests, not proof of sixteen simultaneous 1M prompts. The locally
qualified media contract is up to 16 images and zero videos.

## Evidence by measurement class

- `functional`: complete direct preflight passed, including strict JSON,
  retrieval, tools 20/20, streaming tools, tool-result continuation,
  Responses, image understanding, and OCR. The authenticated routed subset
  passed after promotion with exact served identity.
- `capacity`: exact retrieval passed at a 950K target in 242.0 seconds. C2 at
  500K completed 2/2; the engine reported 2,917,371 KV tokens.
- bounded `quality`: intelligence 6/6, session 3/3, and tools 3/3 passed under
  the recorded high-reasoning request control.
- `performance`: K5 measured 82.1 tok/s at 4K, 67.4 at 131K, and 67.9 at 240K.
  Median TTFT was 1.00, 19.17, and 38.15 seconds respectively.
- `concurrency`: K5 C16 completed 32/32 twice at 35 then 26 aggregate output
  tok/s. K3 completed 32/32 twice at 36 then 42; the short-answer aggregate is
  response-length-sensitive and is not a long-generation decode rate.
- multimodal: the image/OCR corpus passed 12/12; up to 16 images are admitted.
  Video was not inferred from the model name and remains disabled.
- client acceptance: four Hermes profiles each passed text and image, Pi's
  normal extension-loaded PTY image path passed, and OpenClaw's running-gateway
  dynamic-image path passed. The final router decision buffer was 60/60 served
  without fallback.

## Matched local comparison

| Requested context | Prior 262K K5 decode | Current 1M K5 decode | Prior TTFT | Current TTFT |
|---:|---:|---:|---:|---:|
| 4K | 64.5 tok/s | **82.1 tok/s** | 1.08 s | **1.00 s** |
| 32K | 50.5 tok/s | **62.2 tok/s** | 8.19 s | **7.81 s** |
| 65K | 55.2 tok/s | **67.2 tok/s** | 16.60 s | **11.16 s** |
| 131K | 48.0 tok/s | **67.4 tok/s** | 34.39 s | **19.17 s** |
| 240K | 45.7 tok/s | **67.9 tok/s** | 64.32 s | **38.15 s** |

The selected K3 target improves the local 240K median TTFT by 40.7% and
decode by 48.6% while quadrupling the configured context. This comparison is
local to the recorded custom runtime and dual-Max-Q/WSL2 topology; it is not a
general model-intelligence ranking.

## Decision and promotion state

K5 with 2,048-token batching is live as the one-week text/image/OCR default.
K3 remains available as a verified alternate because it produced the stronger
repeat C16 result, but K5 keeps the default because its 4K c1 decode was
82.1 tok/s versus 66.8. Raising batching to 4,096 reduced 4K decode to
62.5 tok/s and produced no C16 benefit, so that arm is rejected.

The 1M route advertises 8,192 maximum output, router c16, and 16 images.
`llm.primary`, `llm.secondary`, `llm.auxiliary`, `llm.voice`,
`vision.general`, and `vision.ocr` select the same exact service during this
evaluation. Qwen3.8 Flash Next is the immediate retained video-capable
rollback; the earlier 262K GLM image/profile is retained as the GLM-specific
rollback.

## Failures and gotchas

- DFlash2's published license is noncommercial/no-derivatives. Obtain separate
  permission before commercial use.
- The target/draft/runtime are community artifacts, not stock-vLLM support.
- Video is unsupported; the DFlash2 drafter receives text-only inputs on image
  calls while the target processes the image.
- WSL2 peer IPC failed, requiring the qualified PyNCCL transport translation.
- The runtime suggested a larger fixed KV pool, but it was not A/B tested; the
  selected 0.95 utilization retains measured operating reserve.
- No missing MoE/GEMM tune warning appeared. First-request JIT warnings are
  warm-up observations, not evidence that a kernel tune would help.
- A forced thinking-off restored probe concatenated hidden reasoning into
  visible content; the same exact service passed with the qualified low
  reasoning request control.
- OpenClaw's managed restart exposed a PATH-resolution product defect. A
  bounded launchd restart restored the service and the real client test passed.
- No exact Docker-image removal product surface exists. The previous GLM image
  remains the intended one-week rollback; no broad prune was used.

## Dated run history

| Date | Event | Result |
|---|---|---|
| 2026-08-30 | Current-source refresh, K5/K3/batch4,096 A/B, 4K-500K performance, 950K retrieval, image/OCR, quality, router, and real-client promotion | K5/batch2,048 selected as `current` one-week default; K3 verified alternate; batch4,096 rejected; full [finding and raw artifacts](../../findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md) |
| 2026-08-29 | Cardillo/Purtell translation, adaptive/fixed/no-spec A/B, vision/OCR, 250K and near-500K capacity | TR3 vision fixed K5 and no-spec 524K qualified as challengers; adaptive MTP rejected; 0xSero 3.0-bpw watch-only; [historical finding](../../findings/2026-08-29-glm53-cardillo-purtell-qualification.md) |
