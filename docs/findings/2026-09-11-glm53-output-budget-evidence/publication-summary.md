<!-- benchmark-publication-summary/v1 -->
# Local GLM output-budget result

Canonical facts: real Pi through Anvil to one unchanged rc14 backend on two RTX PRO 6000 Max-Q GPUs, TP2, 393,216 context, C1, FP8 KV, adaptive EAGLE [3,5], max reasoning. Warm/cache-uncontrolled. Use the [finding](../2026-09-11-glm53-output-budget.md) for the exact recipe and immutable pins.

## Short post

Local GLM/Pi test: 16K rescued one capped answer, but not its repeat. Keep 4K. Eight coding runs invalid. https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-09-11-glm53-output-budget.md

## Reddit title

Local GLM through Pi: a larger completion budget did not pass our repeatability gate

## Reddit body

We tested 4,096 versus 16,384 completion tokens on the same running GLM backend. Valid task successes were 6/8 versus 7/8. The larger budget still ended one reasoning run at its ceiling without a final answer. Eight coding runs were invalid due to fixture contamination and excluded. No default or model setting changed. This small, incomplete panel does not establish a model-wide quality or performance ranking.

## Screenshot alt text

Two-column local comparison: 4K has six of eight valid tasks pass and two empty capped finals; 16K has seven of eight and one. Eight coding runs excluded. No promotion.

## Claim ledger

| Claim | Evidence |
|---|---|
| 6/8 versus 7/8; truncations 2 versus 1 | [Native records](panel-results.json), [validity](validity.json), [summary](summary.json) |
| Same backend and restored default | [Identity](identity.json), [restoration](restoration.json) |
| Fixture gap and no repeated rescue | [Friction](friction-log.md) |
