# Qwen3.8 27B SGLang video qualification and router expansion

Date: 2026-08-16

Decision: `current`, video capability expanded in place

Source revision: `80936484` plus this finding's change

## Outcome

The current official-FP8 Qwen3.8 27B SGLang service now owns explicit video as
well as Primary, general vision, and OCR. The model already accepted OpenAI
`video_url` content, but the promoted router profile declared a zero-video
limit and had not enabled fail-closed media admission. We qualified the
existing model directly, added `vision.video`, enabled router-side admission,
and performed a managed router-only cutover. The model was not restarted and
the second equal GPU remained dormant.

The published contract is concurrency one, at most two images, and at most one
video per request. `vision.general` also preserves video requests to the same
direct tier. Unknown aliases still fail, and there is no classification or
fallback behavior.

## Exact configuration

| Field | Value |
|---|---|
| Model | `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` |
| Engine | SGLang `c4271c3fe1262fc2adbd162c33b25de5255251c5` |
| Runtime | digest `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124` |
| Hardware | one RTX PRO 6000 Blackwell Max-Q, TP=1; second equal card dormant |
| Context / concurrency | 393,216 tokens / one running request |
| Quantization | official FP8 weights, FP8 E4M3 KV |
| Prefill / speculation | 2,048-token chunks; EAGLE steps/top-k/draft `3/1/4` |
| Multimodal transport | CPU feature transport |
| Router admission | two images, one video, fail-closed |

The 2,048 image-token and 16,384 video-token router values are conservative
admission estimates. They are not fixed observed media-token counts. The
deterministic video cases reported between 560 and 11,704 video tokens.

## Results

| Gate | Result |
|---|---|
| Direct full deterministic corpus | **30/30**: image 12/12, video 14/14, mixed 4/4 |
| Direct video latency | p50 **2.935 s**, p95 **9.904 s** across 14 attempts |
| Isolated router, complete corpus | 28/30; only the two four-image-plus-video attempts received the intended 413 |
| Isolated router, admitted subset | **28/28** |
| Live router, admitted subset | **28/28** |
| Two-video overflow | HTTP 413 before upstream |
| Malformed video | sanitized HTTP 400 |
| Video SSE | 200, ordered events, `[DONE]` |
| Video-grounded tool call | `record_sequence` with red then green |
| Post-cutover Primary regression | coding, JSON, tools 20/20, streaming tools, tool recovery, Responses, image, and OCR all passed |

The direct corpus covered temporal order at 10 seconds, a 30-second state
change, 60-second event localization, 120-second continuity, video OCR, two
Creative Commons clips, and mixed video/image requests. The full corpus is
useful model evidence; the admitted subset is the correct routed acceptance
set because the current image ceiling is deliberately two.

## Evidence

- [Direct full corpus](2026-08-16-qwen38-27b-video-router-evidence/direct-full-multimodal-corpus.json)
- [Live routed admitted corpus](2026-08-16-qwen38-27b-video-router-evidence/live-routed-admitted-multimodal-corpus.json)
- [Live temporal video preflight](2026-08-16-qwen38-27b-video-router-evidence/live-routed-temporal-video-preflight.json)
- [Post-cutover Primary regression](2026-08-16-qwen38-27b-video-router-evidence/live-routed-primary-regression-preflight.json)
- [Edge and deployment summary](2026-08-16-qwen38-27b-video-router-evidence/edge-summary.json)

The deterministic corpus SHA-256 is
`ebff9dcc87a7fd13f801fc19eeea7271aec01a99fe560d721be99c1c9becad49`.
The installed router configuration SHA-256 is
`043402e762a54e0d02e6c5981dc654d0717dd2247a6b2ea54f35ea462941741f`.
Raw host-specific evidence and the exact operator profile remain private.

## Decision and limits

Video is accepted as a bounded current capability on the existing service. A
retained no-video managed router profile is the rollback; it changes routing
metadata and admission only, so rollback does not require a model lifecycle
operation.

This qualification does not establish concurrency above one, more than two
images with a video, multiple videos, host-memory-pressure tolerance, or broad
real-world video quality. The two expected 413 responses are policy behavior,
not model failures. The dormant second card was not measured by this change.
