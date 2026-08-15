# Qwen3.8 27B SGLang MTP and multimodal qualification

**Date:** 2026-08-15

**Evidence:** local `functional`, `capacity`, bounded deterministic `quality`,
and single-image/OCR checks on two RTX PRO 6000 Blackwell Max-Q cards

**Decision:** SGLang MTP=3 is qualified as a high-decode TP=1/393K
`challenger`; CPU feature transport qualifies bounded image/OCR on both
checkpoints; all four recipes remain `no-promotion`

**Source base revision:**
`62cdc1da4589d9ad29b09a257c00601cca7682de`

**Sanitized machine-readable record:**
[summary.json](2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification-evidence/summary.json)

## Outcome

The SGLang cookbook's in-checkpoint MTP configuration is a material win on
this host. At the matched 4K/c1 shape, official FP8 rose from 48.0 to 111.3
decode tok/s and Inferact NVFP4 rose from 57.9 to 98.1. MTP reduced median E2E
34.2% for official FP8 and 26.6% for NVFP4, while TTFT and effective prefill
regressed slightly because speculation accelerates decode rather than prompt
processing.

The quantization ranking therefore depends on workload phase. NVFP4 remains
the lower-TTFT, higher-prefill option. Official FP8 becomes the faster decoder
under MTP because its sampled speculative acceptance was higher. Official FP8
accepted about 3.35-3.52 tokens per speculative step at 0.78-0.84 acceptance;
NVFP4 sampled about 2.88-3.12 at 0.62-0.71. These are scheduler-log samples,
not a full-run statistical distribution.

Both MTP candidates passed coding, JSON, a 131K retrieval probe, 20/20 tools,
streaming tools, tool-result recovery, the Responses subset, and a near-limit
389K retrieval probe. Both also passed repeated deterministic intelligence
6/6, session 3/3, and tool 3/3 checks with thinking disabled and no reasoning
leakage.

The earlier SGLang multimodal failure was not a missing vision tower or a
quantized-weight omission. It was the automatically selected CUDA-IPC feature
transport failing to reconstruct a pooled CUDA allocation under this exact
WSL2/Docker/PyTorch/runtime combination with `CUDA error: invalid resource
handle`. Forcing the supported CPU feature transport removed the crash. Both
official FP8 and NVFP4 then passed real image understanding and verbatim OCR
with MTP still enabled.

## Immutable identity and matched configuration

- Official FP8:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Inferact NVFP4:
  `Inferact/Qwen3.8-27B-NVFP4@6128240ebaf4eaa7bad2b3d1c72c37d677c5f462`.
- Runtime:
  `lmsysorg/sglang@sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- Image-label engine revision:
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Upstream cookbook/config revision:
  `dd458f3212dd4ddf0e1a7907bbf539b660e70d21`.
- Hardware: two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards in split
  mode, one TP=1 candidate per card, followed by a physical-card swap.

The common text configuration fixed 393,216 context tokens, one running
request, memory fraction 0.85, FP8 E4M3 KV, FlashInfer attention, 2,048-token
prefill chunks, radix cache disabled, Qwen reasoning/tool parsers, and a
thinking-disabled server default. The MTP arm added SGLang EAGLE steps/top-k/
draft-tokens `3/1/4`. With radix caching disabled, the cookbook's GDN formula
requires one request state plus four verification intermediates, so the Mamba
state cache increased from one to five slots. The pinned image contains
FlashInfer 0.6.18, above the cookbook's stated `0.6.15.post1` MTP floor.

Portable recipes:

- [official FP8 MTP=3](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-fp8-sglang-tp1-393k-mtp3-recipe.toml)
- [Inferact NVFP4 MTP=3](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-inferact-nvfp4-sglang-tp1-393k-mtp3-recipe.toml)
- [official FP8 MTP=3 multimodal CPU transport](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-fp8-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml)
- [Inferact NVFP4 MTP=3 multimodal CPU transport](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-inferact-nvfp4-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml)

## Matched 4K performance

Each row is the mean of five run medians: three repetitions on the first card
placement and two after swapping models across the equal cards. Every run sent
ten requests at concurrency one, 4,096 configured input tokens, and a
256-token output cap.

| Candidate | Speculation | TTFT | Effective prefill | Decode | E2E | Aggregate output |
|---|---|---:|---:|---:|---:|---:|
| Official FP8 | off | 0.554 s | 6,512 tok/s | 48.0 tok/s | 1.451 s | 30.2 tok/s |
| Official FP8 | MTP=3 | 0.569 s | 6,341 tok/s | **111.3 tok/s** | 0.954 s | 46.0 tok/s |
| Inferact NVFP4 | off | **0.429 s** | **8,409 tok/s** | 57.9 tok/s | 1.244 s | 38.4 tok/s |
| Inferact NVFP4 | MTP=3 | 0.448 s | 8,065 tok/s | 98.1 tok/s | **0.914 s** | **50.9 tok/s** |

| Candidate | TTFT delta | Prefill delta | Decode delta | E2E delta | Aggregate delta |
|---|---:|---:|---:|---:|---:|
| Official FP8, MTP versus control | +2.7% | -2.6% | **+131.9%** | **-34.2%** | +52.3% |
| Inferact NVFP4, MTP versus control | +4.2% | -4.1% | **+69.4%** | **-26.6%** | +32.6% |

The card swap preserved the ranking: official FP8 remained about 111 tok/s
decode and NVFP4 about 98 tok/s. NVFP4 retained the better TTFT and prefill.
The result therefore follows the checkpoint/quantization interaction with
MTP, not a physical GPU lane.

The current vLLM official-FP8 MTP=3 service previously measured 93.6 decode
tok/s at its promotion shape. The SGLang official-FP8 MTP result is 18.9%
higher on this matched client-side decode metric, but it does not authorize an
engine or route change. Startup behavior, multimodal breadth, operational
maturity, and the current 32-image contract still differ.

## Functional, quality, and long-context gates

Both text MTP profiles passed:

- short coding and structured JSON;
- 131,072-token retrieval;
- 20/20 shared-prefix tools, streaming tools, and tool-result recovery;
- the supported Responses subset;
- one near-limit 389,000-token retrieval request, completing in 193.2 seconds
  for official FP8 and 185.3 seconds for NVFP4; and
- repeated deterministic intelligence 6/6, session 3/3, and tools 3/3 on the
  final multimodal-capable MTP profiles.

The 389K rows are one request per model and establish bounded retrieval at the
configured limit. They are not p50/p95 latency or broad long-context reasoning
evidence. The deterministic quality slice is not SWE-bench.

## Why multimodal failed, and the bounded fix

Both exact checkpoints contain the conditional-generation architecture,
image/video processors, and 333 `model.visual*` tensors. Official FP8 excludes
the visual modules from its FP8 conversion; Inferact excludes both visual and
MTP modules from NVFP4. Missing vision weights were therefore not the blocker.

The failing request path selected SGLang's CUDA-IPC multimodal feature
transport and then failed in PyTorch shared-CUDA storage reconstruction. A
retry with handle caching disabled failed the same way. The diagnostic
recipes make the minimum functional transport change:

- remove `--language-only`;
- add `--mm-feature-transport cpu`;
- set `SGLANG_USE_CUDA_IPC_TRANSPORT=0` defensively; and
- disable the first-pass vision CUDA graph and extra multimodal buffer with
  `SGLANG_VIT_ENABLE_CUDA_GRAPH=0` and `SGLANG_MM_BUFFER_SIZE_MB=0`.

SGLang intentionally supports CPU transport in
[PR #33899](https://github.com/sgl-project/sglang/pull/33899), while
[PR #32541](https://github.com/sgl-project/sglang/pull/32541) documents the
automatic CUDA-IPC selection and
[PR #33653](https://github.com/sgl-project/sglang/pull/33653) adds capability
gating. The pinned public
[server arguments](https://github.com/sgl-project/sglang/blob/c4271c3fe1262fc2adbd162c33b25de5255251c5/python/sglang/srt/server_args.py)
expose CPU, CUDA-IPC, and CUDA-VMM transports.

This is deliberately not described as “CUDA IPC does not work on WSL.”
NVIDIA documents CUDA IPC support in current WSL drivers. The local evidence
is narrower: SGLang/PyTorch pooled CUDA-IPC reconstruction fails in this exact
container/runtime combination; the lower-layer incompatibility is not yet
isolated.

With CPU transport, both models passed the same dashboard screenshot:

| Candidate | General image | Verbatim OCR | Result |
|---|---:|---:|---|
| Official FP8 MTP=3 | 9.3 s | 0.7 s | pass |
| Inferact NVFP4 MTP=3 | 7.7 s | 0.7 s | pass |

The first 512-token image run found every required detail but ended at the
completion cap and correctly failed the evidence policy. The retained pass
used a 1,024-token allowance and finished normally. This campaign did not run
the full media corpus, video, multiple-image ordering, the 32-image ceiling,
or concurrency above one. CPU transport can also shift pressure to host/WSL
memory; that cost remains unmeasured.

## Retained failures and operational caveats

- The first candidate launch used the globally installed `anvil-serving`
  command. It reported the current package version but executed stale recipe
  behavior, ignored the immutable `model_path`, and sent a mutable Hub repo ID
  into offline mode. Those containers failed before serving and produced no
  benchmark data. Relaunching through the isolated current module used the
  pinned snapshot paths. This is a recurrence of the open
  [editable-install control-plane hazard](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-08-live-cli-is-an-editable-install-on-a-scratch-worktree.md).
- The checkpoint advertises 262,144 tokens, so SGLang warns about the explicit
  393,216 override. The local 389K retrieval passes are the evidence for this
  exact override.
- Both lanes retain the warning that absent FP8 KV scaling factors default to
  1.0. Bounded quality passed; unquantized-KV equivalence remains unproven.
- The image label names SGLang `c4271c3`, while an internal build string names
  `561c8f3`. The image digest is the execution identity.
- The first final Primary readmission returned a transient 503 even though
  direct health and router identity checks were ready. Bounded router logs
  showed only the expected quiesced-tier 503s; an immediate managed retry
  readmitted successfully. Final routed acceptance passed.

## Restoration and decision boundary

All SGLang candidates were removed through managed recipe lifecycle commands.
The exact pre-test split was restored on the original cards and image digest:

- official FP8 TP=1/393K/MTP=3 text Primary; and
- official BF16 TP=1/393K/MTP=3 multimodal/OCR with the existing 32-image
  ceiling.

Final direct FP8 coding, JSON, tools, streaming, tool-result, and Responses
checks passed. Final direct BF16 text, image, and OCR checks passed. Router
expected/observed identities match; `llm.primary`, `vision.general`, and
`vision.ocr` routed checks passed; all tiers are admitting. Shared memory has
zero files and zero reclaimable bytes.

No Hermes or OpenClaw setting was changed or tested. No route, promotion,
context limit, or current recommendation changed. The SGLang MTP and CPU
multimodal recipes are qualified challengers pending broader media, host-memory
pressure, operational, and human promotion gates.

Raw operator artifacts remain private because they contain live endpoints,
GPU identities, and operator paths. The public summary retains the exact model
and image identities, workload shapes, metrics, failures, and restoration
outcome without publishing private topology.
