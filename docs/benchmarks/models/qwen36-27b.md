# Qwen3.6 27B variants and ThinkingCap

## Current status and review date

Historical comparison set. ThinkingCap is a strict-quality control; all
variants are `no-promotion`. Review date: 2026-07-28.

## Immutable identity

- Community NVFP4+MTP: `6f194695406a3bc88a00573187d5b2eecf984a99`.
- Official FP8: `e89b16ebf1988b3d6befa7de50abc2d76f26eb09`.
- Unsloth NVFP4: `ccdaab7e68af2409599b8949a8f2685703c9bae5`.
- ThinkingCap FP8: `e48255afd77b403446332be0f595868337b36591`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark, one candidate at a time.

## Engine, quantization, KV, context, and concurrency recipe

vLLM nightly/0.25.0 paths, FP8 KV, 262,144 served context, five sequences.
Community and official variants used MTP where stated; the validated
ThinkingCap rollback-control recipe disabled MTP.

## Evidence by measurement class

`functional`, `capacity`, `quality`: preflight, tools, context needles,
five-session capacity, repeated ARC/MMLU slices, and protocol comparisons.

## Decision and promotion state

`no-promotion`. ThinkingCap remains a historical strict-quality control, not
the immediate rollback.

## Failures and gotchas

Matched 1K reasoning budgets starved several variants; model-specific 8K
headroom results are not directly comparable. Community identity must stay
pinned. Preserve MTP and prefix-caching boundaries from the exact recipe.

## Dated run history

- [2026-07-13 container recipe](../../findings/2026-07-13-q36-pro6000-container-recipe.md)
- [2026-07-12 variation bakeoff](../../findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md)
- [2026-07-12 protocol-v2 comparison](../../findings/2026-07-12-qwen36-protocol-v2-comparison.md)
- [2026-07-12 baseline](../../findings/2026-07-12-qwen36-27b-eval-baseline.md)
- [2026-07-12 ThinkingCap record](../../findings/2026-07-12-thinkingcap-heavy-promotion.md)
