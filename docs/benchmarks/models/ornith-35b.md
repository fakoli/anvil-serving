# Ornith 1.0 35B

## Current status and review date

Historical specialist; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`deepreinforce-ai/Ornith-1.0-35B-FP8`. The retained July 10 evidence did not
pin a checkpoint commit, so exact-rerun identity is `historical-invalid`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark.

## Engine, quantization, KV, context, and concurrency recipe

NGC vLLM 0.19, compressed-tensors FP8, FP8 KV, 131,072 context, Qwen reasoning
and tool parsers, thinking disabled.

## Evidence by measurement class

`functional`, `capacity`, `quality`, with `historical-invalid` identity:
131K needle, 20/20 tools, session pass, intelligence 1/2, 29.2 tok/s.

## Decision and promotion state

`no-promotion`; retained as an agentic-coding specialist lead.

## Failures and gotchas

Default thinking empties small budgets. Vendor quality claims were not locally
verified and played no role in the decision.

## Dated run history

- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
