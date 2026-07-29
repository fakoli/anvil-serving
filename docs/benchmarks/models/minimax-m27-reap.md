# MiniMax M2.7 REAP

## Current status and review date

Historical measured challenger; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`dervig/m51Lab-MiniMax-M2.7-REAP-139B-A10B-NVFP4`. The July 10 retained
artifacts did not record a reusable checkpoint commit, so the snapshot is
`historical-invalid` for exact reruns.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark.

## Engine, quantization, KV, context, and concurrency recipe

NGC vLLM 0.19, compressed-tensors NVFP4, FP8 KV, 65,536 context, one sequence,
`minimax_m2` tool parser, thinking disabled.

## Evidence by measurement class

`functional`, `capacity`, `quality`, plus `historical-invalid` identity:
context/tools/session/intelligence 2/2 and 97.2 tok/s.

## Decision and promotion state

`no-promotion`; best measured Heavy candidate in that campaign, not a current
recommendation.

## Failures and gotchas

The community prune lacked a retained exact revision, 131K was not tested, and
no reasoning parser was configured.

## Dated run history

- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
