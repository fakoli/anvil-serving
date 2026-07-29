# GPT-OSS 120B

## Current status and review date

Historical capacity/throughput control; `no-promotion`. Review date:
2026-07-28.

## Immutable identity

`openai/gpt-oss-120b`, observed revision
`b5c939de8f754692c1647ca79fbf85e8c1e70f8a`.

## Tested hardware and topology

RTX PRO 6000 on Fakoli Dark as the earlier Heavy baseline.

## Engine, quantization, KV, context, and concurrency recipe

vLLM nightly, native MXFP4, FP8 KV, CUDA graphs, OpenAI tool parser, 131,072
served context. Historical evidence did not retain a comparison-grade sequence
cap.

## Evidence by measurement class

`functional` and `capacity`, including the 183.2 tok/s long-generation control.
The deterministic recheck is not a valid protocol-v3 quality comparison.

## Decision and promotion state

`no-promotion`; retained as a historical throughput control.

## Failures and gotchas

The service did not pin `--revision` in the oldest recipe. Treat the observed
revision as identity evidence, not proof that a future mutable pull is equal.

## Dated run history

- [2026-07-12 deterministic recheck](../../findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md)
- [2026-07-10 baseline](../../findings/2026-07-10-blackwell-local-model-bakeoff.md#current-baselines)
