# Qwen3.8 27B SGLang FP8 single-service promotion

**Date:** 2026-08-15

**Evidence:** local `functional`, `capacity`, `multimodal`, routed, and real-client acceptance on two RTX PRO 6000 Blackwell Max-Q cards

**Decision:** human-approved `current`; one official-FP8 SGLang TP=1 service owns Primary, general vision, and OCR at 393,216 tokens, while the second equal GPU is intentionally empty

**Source base revision:** `4625ebc84b70a882a31a3b90a0bb3a396b7bd6c2`

## Outcome

The exact SGLang configuration selected by the operator is live. It uses the
official Qwen FP8 checkpoint, TP=1, a 393,216-token context window, one running
request, 2,048-token prefill chunks, FP8 E4M3 KV, memory fraction `0.85`, five
GDN state slots, EAGLE MTP steps/top-k/draft-tokens `3/1/4`, and CPU
multimodal feature transport. One RTX PRO 6000 carries the service; the other
has no model owner and reported zero device-memory use after the former BF16
serve was removed.

The guarded promotion passed exact model identity, short coding, structured
JSON, one 108,000-token retrieval probe, and 20/20 tool calls before changing
the router. Direct image/OCR then passed, followed by the complete repeated
six-case image corpus at 18/18, including ordered comparison of two images.
After the final tier migration, routed image/OCR and the same 18/18 corpus also
passed through the explicit vision aliases.

Routed Primary passed coding, JSON, 20/20 tools, streaming tools, and
tool-result recovery. The Responses subset passed separately under the
recipe-level thinking default. Fresh OpenClaw and Hermes client turns returned
their exact sentinels through the existing Primary alias; OpenClaw reported the
Anvil provider, `llm.primary`, the 393,216-token window, and no fallback.

## Immutable configuration

- Model: `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime: `lmsysorg/sglang@sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- Image-label engine revision: `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- Cookbook/config revision: `dd458f3212dd4ddf0e1a7907bbf539b660e70d21`.
- Portable recipe: [official FP8 SGLang 393K MTP=3 CPU-multimodal](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-official-fp8-sglang-tp1-393k-mtp3-mm-cpu-recipe.toml).
- Hardware: one of two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards, with the second card dormant.

The production admission envelope is deliberately narrower than the model's
theoretical capability: one running request, at most two images per request,
and no video. Those limits match the completed tests rather than the retired
BF16 tier's broader 32-image/one-video declaration.

## Performance basis

No new capacity comparison was run during the cutover. The promotion uses the
byte-identical profile measured earlier the same day. Across the matched
consolidation A/B, official FP8 produced 0.577-second median TTFT, 6,261
effective prefill tok/s, 111.4 decode tok/s, and 0.962-second median E2E at the
4K/c1 shape. Its repeated media corpus measured 0.588-second p50 and
1.886-second p95 latency. The live post-promotion 108K retrieval gate completed
in 23.9 seconds and returned the exact needle.

These figures remain configuration-specific. They do not establish
concurrency above one, video support, a 32-image ceiling, broad visual-quality
equivalence, or performance under host-memory pressure.

## Retained failures and correction

The first direct general-image probe contained every expected string but ended
at its 256-token output cap. Repeating it at the already-qualified 1,024-token
multimodal cap completed with `finish_reason=stop`; OCR passed in both runs.

The first routed Responses probe sent the chat-only
`chat_template_kwargs.enable_thinking=false` field and SGLang correctly
rejected that unsupported Responses field. The router's redundant soft default
was removed because the server recipe already defaults thinking off. Routed
Responses then passed with no reasoning leakage. Chat-completions callers can
still request the supported Qwen thinking control explicitly.

## Rollback and boundaries

The former official-FP8 vLLM container is retained stopped. The former BF16
container is absent. A full split rollback first restores BF16 on the empty
card, then runs the guarded promotion rollback to replace SGLang with the old
FP8 service and reinstall the exact split router profile. This ordering keeps
vision available throughout the rollback.

Raw logs, device identities, endpoint addresses, router state, and client
transcripts remain in the private operator repository. Public evidence records
only immutable model/runtime identities, sanitized metrics, pass counts,
admission limits, and the restoration contract.
