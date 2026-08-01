# Nemotron 3 Super 120B

## Current status and review date

Measured single-card and TP=2 challenger; `no-promotion`. Review date:
2026-08-01.

## Immutable identity

`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` revision
`4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6`.

## Tested hardware and topology

Single RTX PRO 6000 historically; two equal RTX PRO 6000 cards in exclusive
TP=2 plus expert parallel for the 2026-08-01 refresh.

## Engine, quantization, KV, context, and concurrency recipe

Historical lane: vLLM nightly, NVFP4, official `super_v3` reasoning parser,
`qwen3_coder` tool parser, `mamba_cache_dtype=float16`, 131,072 context, five
sequences. TP=2 lane: pinned NVIDIA vLLM, native NVFP4, FP8 KV, TP=2 plus
expert parallel, 65,536 context, one admitted request, and MTP disabled.

## Evidence by measurement class

`functional`, `capacity`, `quality`: the historical lane retains preflight,
131K context, five sessions, 20/20 tools, and repeated ARC/MMLU-Pro slices.
The TP=2 refresh passed smoke/JSON/30K retrieval/tools plus extended tools,
and repeated intelligence 6/6, session 3/3, and tools 3/3. Its 32K lane
measured 2.84 s TTFT, 10,025 effective prefill tok/s, and 59.5 tok/s decode;
the 53,820-token lane measured 5.58 s TTFT and 60.0 tok/s decode.

## Decision and promotion state

`no-promotion`; retained as a strong 1K-reasoning-budget control.

## Failures and gotchas

The advertised 1M context was not tested. The TP=2 recipe deliberately serves
65,536 tokens, so its 60K result is not evidence for the historical 131K lane
or the advertised maximum. Shared KV token capacity is not a claim that each
sequence can consume that many tokens.

## Dated run history

- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-12 challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md)
