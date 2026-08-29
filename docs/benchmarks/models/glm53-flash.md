# GLM-5.3-Flash

## Current status and review date

The 2026-08-29 dual-PRO qualification retains the exact
`brandonmusic/GLM-5.3-Flash-tr3-4bpw` conversion as a
text/tools/image/OCR `challenger` with `no-promotion`. Vision-enabled fixed K5
at 262,144 tokens is the preferred interactive GLM recipe. No-speculation at
524,288 tokens is the preferred maximum-context and scheduling-headroom
recipe. Fixed K5 at 524,288 tokens is a qualified single-user experiment.
Adaptive K1-K5 plus ReplaySSM is rejected for repeated tool-call corruption.
The 0xSero 3.0-bpw TP=4 layout is watch-only because its full serve path is
unimplemented and its publisher's quality gate failed. Review date: 2026-08-29.

## Immutable identity

- Checkpoint: `brandonmusic/GLM-5.3-Flash-tr3-4bpw`
- Revision: `5ab363a8dcf6405955fd5f99671e01a1c9fb124b`
- Runtime image:
  `sha256:da5cec95778bf6996660b52e28a6e51737fec69cfc3d508bf298c8a89f273ac5`
- Runtime source:
  `tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx@3bff1d5fdbafcc3d9865abebddbfe1eef435adef`
- Cardillo integration:
  `samuelcardillo/glm-5.3-flash-2x-rtx-pro-6000-blackwell@5b5623ea07f48683f37f3774d8d5b8bf5b04fdf0`
- Runtime-reported vLLM: `0.1.dev20051+g487ecf187`
- Quantization: ShapleyMCG TR3/EXL3 4 bpw weights with NVFP4 DS-MLA KV

The checkpoint and runtime are third-party artifacts. Their exact license and
provenance boundaries are retained in the
[dated finding](../../findings/2026-08-29-glm53-cardillo-purtell-qualification.md).

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards, 96 GB each, under Windows 11,
Docker Desktop, and WSL2. They were assigned exclusively to one TP=2/DCP=2
owner over PCIe without NVLink. Aggregate VRAM is 192 GB, not unified memory.
All locally qualified profiles use the V2 runner, B12x sparse MLA attention,
2,048-token batching, maxseq16, prefix caching, and one managed runtime-cache
volume. The preferred profile enables the visual tower and one-image/OCR
contract while explicitly disabling video.

## Engine, quantization, KV, context, and concurrency recipe

| Recipe | Role | Context | Decision |
|---|---|---:|---|
| [vision fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | preferred interactive GLM with image/OCR | 262,144 | `verified`, `challenger`, `no-promotion` |
| [text fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | matched text-only performance/control lane | 262,144 | `verified`, `no-promotion` |
| [no spec](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) | preferred maximum context/headroom | 524,288 | `verified`, `challenger`, `no-promotion` |
| [fixed K5](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) | single-user near-500K experiment | 524,288 | `verified`, `challenger`, `no-promotion` |
| [no spec](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | matched reliability/control lane | 262,144 | `verified`, `no-promotion` |
| [adaptive K1-K5 plus ReplaySSM](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-adaptive-mtp-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) | external-recipe reproduction | 262,144 | `rejected` |

All recipes pin the exact image and checkpoint. The WSL2 translation disables
only the failing cuMem, expandable allocator, CUDA-IPC custom all-reduce,
DCP A2A, and lazy top-K owner-exchange paths while preserving the custom
EXL3/B12x compute and NVFP4 KV path.

## Evidence by measurement class

- `functional`: vision fixed K5 passed smoke, JSON, retrieval, image
  understanding, verbatim OCR, tools 20/20, streaming tools, tool-result
  continuation, and Responses. Video was disabled and multiple images were
  not qualified.
- `capacity`: both 524K profiles recovered the exact needle at 495,045 actual
  prompt tokens and produced a valid tool call at 497,976. No-spec reported
  1,603,111 KV tokens; fixed K5 reported 565,898.
- bounded `quality`: vision fixed K5 passed 15/15 attempts across the five-item
  high-reasoning coding-agent suite and 20/20 high-reasoning tools. The
  requested reasoning control is recorded, not independently proven by the
  server.
- `performance`: vision fixed K5 at 262K measured 72.8 tok/s decode at 4K and
  55.7 tok/s at 128K. The matched text-only arm measured 69.8 and 61.9 tok/s.
  The current Qwen Primary remains more than twice as fast in the directional
  same-host reference at 128K.
- `concurrency`: vision fixed K5 completed 16/16 short requests at 28.3
  aggregate output tok/s. Its 560,866-token KV pool supports 2.14 complete
  configured windows, not c16 full-window capacity.
- negative `quality`: adaptive MTP completed 12/20 repeated tools and 13/15
  low-reasoning coding attempts, with degenerate repeated `handle` output.

## Decision and promotion state

This model is not routed or promoted. The current Qwen3.8 Flash Next Primary
keeps its text/image/OCR/video contract. GLM's local advantages are a reliable
text/tool/image/OCR profile and near-500K text-prompt capacity; its
disadvantages are materially lower decode, higher long-context TTFT, disabled
video, community artifact provenance, and no real routed-client acceptance.

Hands-on use should begin with vision fixed K5 at 262K. Use no-spec at 524K
when near-500K context or wider KV headroom matters. The 524K fixed-K5 recipe
is bounded to deliberate single-user use because it retains only 1.08 complete
configured KV windows.

## Failures and gotchas

- The native-Linux recipe required six bounded WSL2 transport/allocator
  changes before the V2/NVFP4 path could complete a 128K prefill.
- The V1 fallback loaded the target but could not preserve the selected
  MTP/NVFP4 combination.
- FP8 KV was incompatible with the required runtime block-size path.
- Adaptive MTP was the fastest measured 4K arm but failed correctness and must
  not be inferred as the recommended recipe.
- Low-reasoning reliable profiles missed one Windows-safe recursive-move plan
  attempt; the fixed 524K high-reasoning suite passed 15/15.
- The 524K fixed profile's narrow KV/VRAM margin is not a concurrency
  qualification.
- Vision uses a hash-gated repaired chat template because the exact checkpoint
  snapshot's shipped template strips media markers. The snapshot and shared
  blobs remain immutable.
- The MTP drafter receives text-only inputs on multimodal requests while the
  target model receives the image; image-call speculative benefit may differ.
- The 0xSero 3.0-bpw release requires an unpublished custom selective-EXL3
  TP=4 server and reports a failed held-out PPL/KL gate. It was not downloaded
  or served in this campaign.

## Dated run history

| Date | Event | Result |
|---|---|---|
| 2026-08-29 | Current-source research, feasibility, WSL2 translation, adaptive/fixed/no-spec A/B, vision/OCR, 250K and near-500K capacity, tools, bounded coding, concurrency | vision fixed K5 interactive and no-spec 524K maximum-context profiles qualified as `challenger`/`no-promotion`; adaptive MTP rejected; 0xSero 3.0-bpw watch-only; direct vision candidate left running for hands-on |
