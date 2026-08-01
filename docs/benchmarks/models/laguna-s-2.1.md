# Laguna S 2.1 and Laguna XS

## Current status and review date

Laguna S 2.1 is the immediate managed `rollback`. Laguna XS is `rejected`.
The dual-PRO TP=2 lane is qualified as `no-promotion`. Review date: 2026-08-01.

## Immutable identity

Laguna S: `poolside/Laguna-S-2.1-NVFP4` revision
`07614121b31898586430f189d27a25a0be310843`. Laguna XS evidence retains its
attempted identity in the July 12 finding; it never produced a qualified recipe.

## Tested hardware and topology

Single RTX PRO 6000 historically; two equal PRO 6000 cards in exclusive TP=2
for the 2026-08-01 refresh. Both configurations used isolated endpoints.

## Engine, quantization, KV, context, and concurrency recipe

Laguna S uses vLLM `0.23.1rc1.dev1327+gf25953cc5`, pinned nightly image,
NVFP4/FP8 KV, 262,144 context, and thinking disabled. Laguna XS FP8-KV and
fallback attempts were compatibility probes only.
The TP=2 lane uses pinned vLLM 0.25.1, the same exact Laguna S checkpoint,
NVFP4/FP8 KV, 262,144 context, one admitted request, no DFlash, and thinking
disabled.

## Evidence by measurement class

Laguna S has `functional`, `capacity`, and `quality` evidence: repeated
protocol-v3 and 32K/128K/240K retrieval passed. Laguna XS has
`compatibility-only` and `historical-invalid` evidence.
The TP=2 refresh passed smoke/JSON/30K retrieval/tools plus extended tools,
repeated intelligence 6/6, session 3/3, and tools 3/3. At 32K it measured
1.97 s TTFT, 15,134 effective prefill tok/s, and 70.9 tok/s decode. At a
231,457-token prompt it measured 31.85 s TTFT, 7,252 effective prefill tok/s,
and 66.0 tok/s decode.

## Decision and promotion state

Laguna S remains `rollback`; the TP=2 campaign is `no-promotion`. Laguna XS is
`rejected`.

## Failures and gotchas

Do not enable thinking for the managed Laguna S contract. Laguna XS produced
corrupted text/0-of-20 tools with FP8 KV and stalled on a non-FP8 fallback.

## Dated run history

- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-26 Laguna S qualification](../../findings/2026-07-26-laguna-s-heavy-qualification.md)
- [2026-07-12 Laguna XS evaluation](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
