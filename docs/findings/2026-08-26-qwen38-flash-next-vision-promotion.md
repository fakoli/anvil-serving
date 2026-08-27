# Qwen3.8 Flash Next vision promotion and 262K benchmark

**Date:** 2026-08-26

**Scope:** two RTX PRO 6000 Blackwell Max-Q cards, WSL2, exclusive TP=2,
QSA-fast MTP3, text/image/video, 262,144 tokens, concurrency one

**Decision:** expand the current Primary in place to `vision.general`,
`vision.ocr`, and `vision.video`; admit at most four images or one video per
request

## Outcome

The already-promoted
`RadixArk/Qwen3.8-Flash-Next-NVFP4@7b719225242aacd3dbd3f9407468c2ee9a9d2594`
passed the full direct multimodal corpus 30/30. The authenticated live router
then passed media admission, malformed-media handling, SSE video, grounded
video tool use, OCR, and the complete post-media Primary regression suite. The
same service now backs text Primary and the three explicit vision aliases; the
router does not infer media intent or provide a fallback.

This is the strongest bounded multimodal result currently recorded for this
exact model and host. It is not a claim that the model is generally strongest,
lossless, or better than untested checkpoints.

## Shareable configuration

| Item | Qualified value |
|---|---|
| Model | RadixArk Qwen3.8 Flash Next NVFP4, revision `7b719225` |
| Runtime | SGLang `d91c3682`, image `sha256:59f06adc`, exact PR #36556 SM120 QSA gate |
| Hardware | 2x RTX PRO 6000 Blackwell Max-Q, TP=2 over PCIe without NVLink, WSL2 |
| Context / concurrency | 262,144 tokens / c1 |
| Speculation | NEXTN steps/top-k/draft `3/1/4` |
| KV | BF16/auto; 6.275 GiB per TP rank, 12.55 GiB aggregate; 516,032 server tokens |
| Static GPU memory | 0.80; startup reported 19.008 GiB available and 65.031 GiB weights per rank |
| Media contract | text/image/video; four images or one video; fail-closed admission |
| Thinking | disabled for the qualified route contract |

Aggregate VRAM is sharded capacity, not one unified 192 GiB device. The
516,032-token allocation is 1.969 full 262,144-token windows in arithmetic,
but the qualified scheduler and router contract remain c1. Two full windows
would require 524,288 tokens, 8,256 more than the measured pool.

## Context and throughput sweep

All 25 requests completed. Runs used concurrency one, temperature zero, a
512-token output request, and the same running service. `Effective prefill` is
usage prompt tokens divided by client-observed time to first output, so it
includes scheduling and first-token work; it is not a raw engine kernel rate.
Each cell shows p50 followed by the observed range.

| Target | Actual prompt p50 | Reps | TTFT s | Effective prefill tok/s | Decode tok/s | E2E s |
|---:|---:|---:|---:|---:|---:|---:|
| 4,096 | 3,613 | 5 | 0.141 (0.137-0.643) | 25,627 (5,623-26,467) | 155.9 (136.9-160.3) | 0.340 (0.330-0.862) |
| 32,768 | 29,797 | 5 | 2.247 (2.237-2.596) | 13,261 (11,475-13,319) | 130.1 (118.5-156.5) | 2.569 (2.502-2.926) |
| 65,536 | 61,681 | 5 | 3.255 (3.245-3.261) | 18,953 (18,913-19,010) | 132.4 (115.8-147.2) | 3.453 (3.434-3.551) |
| 131,072 | 125,447 | 5 | 6.932 (6.898-8.847) | 18,096 (14,180-18,186) | 114.7 (109.1-124.2) | 7.192 (7.108-9.058) |
| 196,608 | 189,209 | 3 | 20.066 (19.779-20.400) | 9,429 (9,275-9,566) | 120.5 (119.4-138.3) | 20.257 (20.068-20.592) |
| 253,952 | 245,000 | 2 | 27.345 (27.345-27.410) | 8,938 (8,938-8,960) | 112.9 (112.9-152.0) | 27.549 (27.549-27.732) |

The separate full-reserve gate remains the context-contract proof: 253,703
actual prompt tokens with an 8,192-token output request, 29.214 s TTFT, 8,684
effective prefill tok/s, 102.0 decode tok/s, and 8,441 physical tokens
remaining. The 245,000-token sweep row is a performance sample with only a
512-token output request and must not replace that reserve proof.

Machine-readable p50, p95, and per-request timings are retained in the
[six capacity artifacts](2026-08-26-qwen38-flash-next-vision-promotion-evidence/README.md#context-and-throughput).

## Vision corpus

The hash-pinned `agents-a1-v1` corpus contains 15 cases repeated twice: six
image cases, seven video cases, and two mixed image/video cases. It covers OCR,
charts, UI text, spatial count, multiple-image comparison, temporal order,
state change, event localization, video OCR, 120-second continuity, two
Creative Commons clips, and mixed requests up to four images plus one video.

| Path | Strict score | Image | Video | Mixed | Result boundary |
|---|---:|---:|---:|---:|---|
| Direct model endpoint | 30/30 | 12/12 | 14/14 | 4/4 | complete pass |
| Isolated router, repeat 1 | 27/30 | 12/12 | 11/14 | 4/4 | three literal-rubric misses |
| Isolated router, repeat 2 | 30/30 | 12/12 | 14/14 | 4/4 | complete pass |
| Live router, repeat 1 | 29/30 | 12/12 | 13/14 | 4/4 | one literal-rubric miss |
| Live router, repeat 2 | 28/30 | 12/12 | 12/14 | 4/4 | two literal-rubric misses |

The live repeatability score is therefore 57/60 strict. In every miss, the
answer was transport-complete and semantically correct: it localized the event
at 42-47 seconds but omitted the expected literal word `alert`, or described
the yellow/green/orange/yellow changes but omitted the literal word `color`.
Those are retained failures, not silently reclassified passes.

Direct end-to-end latency by modality was:

| Modality | Attempts | Prompt media-token range | Latency p50 | Latency p95 |
|---|---:|---:|---:|---:|
| Image | 12 | 220-440 image tokens | 0.636 s | 2.007 s |
| Video | 14 | 560-11,704 video tokens | 1.236 s | 4.274 s |
| Mixed | 4 | 220-880 image plus 560-1,680 video tokens | 1.147 s | 1.485 s |

The four-image-plus-video case consumed 880 image and 1,680 video tokens. The
two Creative Commons clips were the largest media prompts at 11,594 and 11,704
video tokens.

## Router and client closure

Both isolated and live router edge suites passed 8/8:

- one video plus four images was admitted;
- five images and two videos were rejected with 413;
- malformed image and video payloads returned sanitized 400 responses;
- video SSE preserved ordered content and the terminal event;
- a video-grounded tool call preserved structured `red`, then `green`
  arguments; and
- unsupported Anthropic-to-OpenAI video translation failed closed.

After cutover, `llm.primary` passed smoke, JSON, tools 20/20, streaming tools,
tool-result continuation, Responses, image, and video. `vision.ocr` separately
returned the exact retained OCR markers. The installed client catalogs already
pointed Hermes, Pi, and OpenClaw at `vision.general` with a 262,144-token,
8,192-output declaration, so their synchronized files required no content
change. Fresh real-client image turns are retained as a separate sanitized
acceptance artifact.

## What to test next

These are experiment candidates, not recommended production changes:

1. **128K c2:** the 516,032-token pool is ample for two 131,072-token windows,
   but the current scheduler and router deliberately cap concurrency at one.
   Qualify c2 with an otherwise identical recipe and repeat media plus text
   latency gates.
2. **Full-window c2 memory:** two 262,144-token windows miss the current pool by
   8,256 tokens. Test one static-memory-fraction step at a time, with WDDM
   reserve sampling and identical quality/capacity gates; do not infer safety
   from aggregate VRAM.
3. **Multimodal preprocessing/cache controls:** A/B SGLang multimedia worker
   count and multimodal cache/prefix-cache controls on repeated and unique
   image/video batches. The current result is c1 request latency, not a media
   concurrency benchmark.
4. **Speculation depth:** compare another NEXTN depth or adaptive policy only
   against the exact no-spec and MTP3 controls. Re-run strict vision, tools,
   long context, and visible-answer gates.
5. **KV dtype:** FP8 KV remains excluded for this pinned SM120/QSA runtime due
   to the recorded SGLang compatibility issue. Treat a later fix as a fresh
   recipe, not a flag toggle.
6. **vLLM/PLE offload:** the shared vLLM recipe uses a different Inferact
   checkpoint and PLE CPU-offload envelope. It remains an external prior until
   the exact local memory, cleanup, compatibility, and quality gates pass.

The exact model revision is on the
[RadixArk revision tree](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4/tree/7b719225242aacd3dbd3f9407468c2ee9a9d2594).
The SM120 fast path is pinned to
[SGLang PR #36556](https://github.com/sgl-project/sglang/pull/36556), the MTP
preset to [cookbook PR #36496](https://github.com/sgl-project/sglang/pull/36496),
and the current FP8-KV limitation to
[SGLang issue #36545](https://github.com/sgl-project/sglang/issues/36545).
These sources are recipe and compatibility evidence; every qualification and
performance number above is local.

## Evidence boundary

The [evidence manifest](2026-08-26-qwen38-flash-next-vision-promotion-evidence/README.md)
links the sanitized raw artifacts and a compact machine-readable summary. Raw
artifacts retain failed outputs, timings, exact model/runtime identity, corpus
hashes, and measurement definitions. They retain no bearer tokens, media
bytes, data URLs, private addresses, GPU UUIDs, or personal paths.

The corpus is deterministic and useful for transport, grounding, OCR, and
repeatability. It is not a broad academic vision benchmark. The Creative
Commons cases are supplementary generalization evidence. Promotion applies
only to the exact c1, thinking-disabled, four-image/one-video contract.
