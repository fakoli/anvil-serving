# GPT-OSS Puzzle 88B

## Current status and review date

Secondary pinned `rollback` with a strict-quality caveat. Review date:
2026-07-28.

## Immutable identity

`nvidia/gpt-oss-puzzle-88B` revision
`9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`; qualified Anvil vLLM engine
commit `485463b3498ed3ffcf0c8fcb52c1670a21be5d82`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark through an isolated serve and then managed
Heavy/router qualification.

## Engine, quantization, KV, context, and concurrency recipe

Native Puzzle architecture, Harmony template, OpenAI tool parser, automatic
tool choice, Marlin MXFP4 MoE, FP8 KV, 131,072 context, 8 sequences. Preserve
the explicit EOS override and V2-runner disable in the operator recipe.

## Evidence by measurement class

`functional`, `capacity`, `quality`: context, tools, session, timeout triage,
and bounded throughput. Strict unified-diff formatting passed only 2/3.

## Decision and promotion state

Pinned secondary `rollback`, after Laguna S 2.1 in the current chain.

## Failures and gotchas

Do not substitute a stock engine or silently retag. The earlier 65.72% GPQA
artifact used the pre-final image and is supporting history, not a final-image
rerun. No general superiority over Gemma is claimed.

## Dated run history

- [2026-07-18 Heavy enablement](../../findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md)
- [2026-07-17 qualification](../../findings/2026-07-17-gpt-oss-puzzle-qualification.md)
- [Canonical operator recipe](../gpt-oss-puzzle-88b-recipe.md)
