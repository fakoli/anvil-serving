# Qwen3.8 27B SGLang single-service consolidation A/B

**Date:** 2026-08-15

**Evidence:** local `functional`, matched `capacity`, and deterministic
multi-image `multimodal` checks on two RTX PRO 6000 Blackwell Max-Q cards

**Decision:** official FP8 is the preferred single-service consolidation
candidate; Inferact NVFP4 is the faster-prefill third-party alternative; both
remain `no-promotion` and the current FP8-text plus BF16-vision split remains
unchanged

**Source base revision:**
`3a2b6e405894d4ea39270518ab05d6b57631d50d`

**Sanitized machine-readable record:**
[summary.json](2026-08-15-qwen38-27b-sglang-consolidation-ab-evidence/summary.json)

## Outcome

The official FP8 checkpoint does not need a separate BF16 checkpoint for the
bounded image workloads tested here. With multimodal CPU feature transport
enabled, official BF16, official FP8, and Inferact NVFP4 each passed the same
18/18 corpus: scene understanding, verbatim OCR, chart reading, UI reading,
spatial counting, and ordered comparison of two images. All three also passed
short coding, structured JSON, 20/20 tools, streaming tools, tool-result
recovery, the Responses subset, image understanding, and OCR with thinking
disabled and no reasoning leakage.

Quantization improved speed without changing the bounded pass result. Official
FP8 reduced median corpus latency 35.8% versus BF16 and raised 4K decode from
62.7 to 111.4 tok/s. NVFP4 reduced median corpus latency 51.1% versus BF16,
cut text TTFT in half, and doubled effective prefill, though its 97.7 tok/s
decode remained 12.3% below official FP8.

That makes official FP8 the best next consolidation candidate: it keeps
official Qwen provenance, delivers the fastest decode, and passed every tested
vision case. NVFP4 is the best latency/throughput candidate when third-party
checkpoint provenance is acceptable. This result is not sufficient to retire
BF16 yet because the test covered concurrency one, at most two images per
request, and no video, 32-image ceiling, host-memory-pressure soak, or broad
vision-quality benchmark.

## Immutable identity and matched configuration

- Official BF16:
  `Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
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
- Hardware: two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards in split mode.

All arms used TP=1, 393,216 configured context tokens, FP8 E4M3 KV, one
running request, memory fraction 0.85, FlashInfer attention, 2,048-token
prefill chunks, radix cache disabled, five GDN state slots, Qwen reasoning and
tool parsers, EAGLE MTP steps/top-k/draft-tokens `3/1/4`, thinking disabled,
and explicit CPU multimodal feature transport. BF16 remained on one card;
official FP8 and NVFP4 ran sequentially on the other card. The earlier
cross-card MTP campaign reproduced the quantized ranking on both placements,
but BF16 was not swapped in this short follow-up.

Portable assets:

- [official BF16 multimodal recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-bf16-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml)
- [official FP8 multimodal recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-fp8-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml)
- [Inferact NVFP4 multimodal recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-inferact-nvfp4-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml)
- [matched public image corpus](https://github.com/fakoli/anvil-serving/blob/main/benchmarks/corpora/agents-a1-v1/qwen38-image-ab.json)

## Matched results

The multimodal corpus used six cases with three repetitions each at
concurrency one, a two-image admission ceiling, and a 1,024-token output cap.
Text values are the mean of three run medians; each run sent ten requests at
concurrency one with 4,096 configured input tokens and a 256-token output cap.

| Candidate | Media pass | Media p50 | Media p95 | TTFT | Effective prefill | Decode | E2E | Aggregate output |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Official BF16 | 18/18 | 0.915 s | 2.723 s | 0.910 s | 3,966 tok/s | 62.7 tok/s | 1.597 s | 27.0 tok/s |
| Official FP8 | 18/18 | 0.588 s | 1.886 s | 0.577 s | 6,261 tok/s | **111.4 tok/s** | 0.962 s | 45.8 tok/s |
| Inferact NVFP4 | 18/18 | **0.448 s** | **1.727 s** | **0.453 s** | **7,975 tok/s** | 97.7 tok/s | **0.918 s** | **50.8 tok/s** |

| Comparison | Media p50 | TTFT | Prefill | Decode | E2E | Aggregate output |
|---|---:|---:|---:|---:|---:|---:|
| Official FP8 versus BF16 | -35.8% | -36.6% | +57.9% | +77.7% | -39.7% | +69.6% |
| Inferact NVFP4 versus BF16 | -51.1% | -50.3% | +101.1% | +55.8% | -42.5% | +88.1% |
| Inferact NVFP4 versus official FP8 | -23.8% | -21.5% | +27.4% | -12.3% | -4.5% | +10.9% |

Observed device allocation after readiness was about 81.7-82.1 GiB for BF16,
83.2 GiB for official FP8, and 83.5 GiB for NVFP4. These are static-cache
runtime allocations at the same memory fraction, not model-weight sizes; they
show that every profile fits one 96 GB card but do not imply that quantized
weights are larger.

## What this proves and what remains

This campaign closes the earlier single-image limitation. It adds a repeated,
hashed corpus and proves two-image ordering on all three checkpoints. It also
shows that the official FP8 checkpoint retains the complete vision path when
SGLang is not launched in language-only mode.

It does not yet prove production equivalence to the current BF16 Omni service.
Before consolidation, the official FP8 candidate still needs:

1. the existing 30-case image/video/mixed-media corpus, including video;
2. the current 32-image request and a useful multi-image ceiling;
3. concurrency and host/WSL memory-pressure measurements with CPU transport;
4. repeated broader vision-quality checks, not only deterministic assertions;
5. router admission plus Hermes/OpenClaw client acceptance; and
6. a human promotion decision with the current split as rollback.

## Restoration and retained caveats

The temporary SGLang candidates were removed through managed recipe lifecycle
commands. Shared memory reported zero files and zero reclaimable bytes. The
exact pre-test vLLM split was restored on its original cards: official FP8
TP=1/393K/MTP=3 Primary and official BF16 TP=1/393K/MTP=3 multimodal/OCR with
the 32-image ceiling.

Both restored services passed direct functional checks; BF16 also passed image
and OCR. Primary readmitted successfully. The first simultaneous Omni readmit
returned a transient 503 while router identity already reported ready; a
sequential managed retry succeeded. Final router expected/observed identities
matched, every tier was admitting, and routed `llm.primary` plus `vision.ocr`
sentinels passed.

`router fleet-status` still reported its configured upstream URLs unreachable
from the command host even while live transition identity and real routed
requests passed. That diagnostic mismatch is not used as health evidence here
and is retained in the
[runtime-perspective ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-08-15-router-fleet-status-runtime-relative-upstreams.md).
No Hermes or OpenClaw configuration changed, and no route or promotion was
made.

Raw operator artifacts remain private because they include live endpoints,
GPU identities, and operator paths. The public summary retains only exact
model/runtime identities, workload shapes, sanitized metrics, pass counts,
and the restoration outcome.
