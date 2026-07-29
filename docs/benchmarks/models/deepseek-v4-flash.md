# DeepSeek V4 Flash

## Current status and review date

Incomplete compatibility attempt; `rejected` under the tested stack. Review
date: 2026-07-28.

## Immutable identity

`nvidia/DeepSeek-V4-Flash-NVFP4`. The aborted run did not retain a reusable
checkpoint revision; identity is `historical-invalid`.

## Tested hardware and topology

RTX PRO 6000 on Fakoli Dark during isolated load attempts.

## Engine, quantization, KV, context, and concurrency recipe

NGC vLLM rejected the `deepseek_v4` architecture. A nightly began loading the
NVFP4 checkpoint but was stopped at shard 18/46.

## Evidence by measurement class

`compatibility-only` and `historical-invalid`; no functional, capacity, or
quality result.

## Decision and promotion state

`rejected` for the tested stack. This is not a general model-quality judgment.

## Failures and gotchas

Do not convert the partial load into a throughput or fit claim. A future retry
needs a pinned compatible engine, exact checkpoint revision, and full gates.

## Dated run history

- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
