# Qwen3.8 27B

## Current status and review date

Official BF16 multimodal and official FP8 text `challenger`;
`no-promotion`. Review date: 2026-08-14.

## Immutable identity

- Official BF16 revision:
  `Qwen/Qwen3.8-27B@1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`.
- Official FP8 revision:
  `Qwen/Qwen3.8-27B-FP8@017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- Runtime image digest:
  `sha256:4a2f33a884222f7049b983263ad9976f89452bb81affecf5b67d89ad35c1bc31`;
  vLLM revision `3a0914114705fa38d4c3171d0746c1a6b6f10209`.

## Tested hardware and topology

One TP=1 serve on each of two equal 96 GB RTX PRO 6000 Blackwell Max-Q cards
in split mode. The cards are independent PCIe devices; aggregate VRAM is not
unified memory.

## Engine, quantization, KV, context, and concurrency recipe

BF16 multimodal used BF16 weights, FP8 KV, 262,144 native context, and two
sequences. Official FP8 text used FP8 weights and KV, the same native context,
and five sequences. Both used vLLM V1 with chunked prefill, no prefix caching,
and no speculative decoding for the control. MTP=3, prefix caching, and
unquantized KV were isolated one-variable arms.

The extended-context arm kept official FP8 weights, FP8 KV, TP=1, chunked
prefill, no prefix caching, and no MTP, but configured 1,010,000 tokens with
one admitted sequence and the official nested `text_config` override.

## Evidence by measurement class

Both official variants passed the thinking-disabled functional gate, repeated
coding/tool/session checks, adaptive reasoning-control probes, and retrieval
through 241,250 actual prompt tokens. BF16 passed 30/30 image/video/mixed-media
attempts. Official FP8 measured 47.9 tok/s c1 decode and 51 aggregate output
tok/s at c5, versus BF16's 26.9 tok/s c1 and 27 aggregate output tok/s at c2.

On official FP8, MTP=3 increased c1 decode to 94.8 tok/s and retained the
repeated quality gate; prefix caching reduced a repeated 30K-prefix c5 burst
from 16.39 seconds TTFT with caching disabled to 0.41 seconds warm; unquantized
KV retained correctness and 244,573-token retrieval but halved reported
full-window capacity from 6.96 to 3.55 windows without a 4K speed gain.

The 1M-configured continuation passed a monotonic retrieval ladder through
825,049 actual prompt tokens. The largest point passed 3/3 with exact output
and a 956.739-second mean request-to-completion latency. A full post-stress gate
also passed. This is stable offline/batch capacity evidence, not an interactive
latency result or proof of a one-million-token API prompt.

Evidence classes are `functional`, `capacity`, bounded `quality`, and
multimodal. The deterministic API checks are not SWE-bench evidence.

## Decision and promotion state

`challenger`, `no-promotion`. Official FP8 plus MTP=3 is the measured
interactive leader within this campaign; BF16 remains the feature reference
for native image and video work. No router alias or client configuration
changed.

## Failures and gotchas

- Official FP8 startup warned that absent attention q/prob scaling factors
  defaulted to 1.0. No independent quality result proves equivalence to
  unquantized KV.
- vLLM warned that 4,096 batched tokens may be suboptimal with MTP=3. A later
  tune must be a matched one-variable A/B.
- The durable separate-worker context/agentic/SWE campaign was not submitted
  because no candidate router alias was approved. Direct API checks are not
  SWE-bench evidence.
- No router alias, client configuration, or promotion changed.
- The 1M-configured retrieval harness produced at most 825,049 API-reported
  prompt tokens, and each largest run took almost 16 minutes.

## Dated run history

- [2026-08-14 official FP8 1M-context continuation](../../findings/2026-08-14-qwen38-27b-1m-context.md)
- [2026-08-14 official BF16/FP8 qualification](../../findings/2026-08-14-qwen38-27b-official-qualification.md)
