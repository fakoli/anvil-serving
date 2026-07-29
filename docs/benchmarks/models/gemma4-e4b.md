# Gemma 4 E4B Fast

## Current status and review date

Historical RTX 5090 Fast control; `no-promotion` in the current Omni topology.
Review date: 2026-07-28.

## Immutable identity

The promoted control was `leon-se/gemma-4-E4B-it-FP8-Dynamic`, pinned in its
dated finding as revision prefix `56e30bf6…`. The earlier GGUF candidate used
`unsloth/gemma-4-E4B-it-qat-GGUF` UD-Q4_K_XL. The old abbreviated revision is
insufficient for a new equivalence claim.

## Tested hardware and topology

RTX 5090 on Fakoli Dark as the former Fast tier.

## Engine, quantization, KV, context, and concurrency recipe

Pinned vLLM Gemma multimodal path for the FP8 control; llama.cpp for the GGUF
latency candidate. The control served a 32K Fast window.

## Evidence by measurement class

`functional`, `capacity`, and `quality` across promotion-era router checks,
template controls, and low-latency measurements.

## Decision and promotion state

Historical control only; `no-promotion` under the current Omni topology.

## Failures and gotchas

Do not treat uncalibrated router seed rows as benchmark results. The July 27
official Gemma access probe failed authorization and loaded no weights.

## Dated run history

- [2026-07-16 template bakeoff](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md)
- [2026-07-13 router promotion-era record](../../findings/2026-07-13-e4b-fast-router-promotion.md)
- [2026-07-10 GGUF bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
