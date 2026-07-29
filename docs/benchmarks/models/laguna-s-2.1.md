# Laguna S 2.1 and Laguna XS

## Current status and review date

Laguna S 2.1 is the immediate managed `rollback`. Laguna XS is `rejected`.
Review date: 2026-07-28.

## Immutable identity

Laguna S: `poolside/Laguna-S-2.1-NVFP4` revision
`07614121b31898586430f189d27a25a0be310843`. Laguna XS evidence retains its
attempted identity in the July 12 finding; it never produced a qualified recipe.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark, isolated candidate/Heavy endpoints.

## Engine, quantization, KV, context, and concurrency recipe

Laguna S uses vLLM `0.23.1rc1.dev1327+gf25953cc5`, pinned nightly image,
NVFP4/FP8 KV, 262,144 context, and thinking disabled. Laguna XS FP8-KV and
fallback attempts were compatibility probes only.

## Evidence by measurement class

Laguna S has `functional`, `capacity`, and `quality` evidence: repeated
protocol-v3 and 32K/128K/240K retrieval passed. Laguna XS has
`compatibility-only` and `historical-invalid` evidence.

## Decision and promotion state

Laguna S is `rollback`, behind current Qwen3.5. Laguna XS is `rejected`.

## Failures and gotchas

Do not enable thinking for the managed Laguna S contract. Laguna XS produced
corrupted text/0-of-20 tools with FP8 KV and stalled on a non-FP8 fallback.

## Dated run history

- [2026-07-26 Laguna S qualification](../../findings/2026-07-26-laguna-s-heavy-qualification.md)
- [2026-07-12 Laguna XS evaluation](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
