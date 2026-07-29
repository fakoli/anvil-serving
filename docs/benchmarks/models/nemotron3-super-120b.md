# Nemotron 3 Super 120B

## Current status and review date

Historical measured challenger; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` revision
`4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark.

## Engine, quantization, KV, context, and concurrency recipe

vLLM nightly, NVFP4, official `super_v3` reasoning parser, `qwen3_coder` tool
parser, `mamba_cache_dtype=float16`, 131,072 context, five sequences.

## Evidence by measurement class

`functional`, `capacity`, `quality`: preflight, 131K context, five sessions,
20/20 tools, repeated ARC and MMLU-Pro slices.

## Decision and promotion state

`no-promotion`; retained as a strong 1K-reasoning-budget control.

## Failures and gotchas

The advertised 1M context was not tested. Shared KV token capacity is not a
claim that each sequence can consume that many tokens.

## Dated run history

- [2026-07-12 challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md)
