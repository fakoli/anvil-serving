# Qwen3.6 27B variants and ThinkingCap

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Historical four-checkpoint Qwen3.6 27B comparison;
      ThinkingCap is retained as a strict-quality control.
    - **Selected or best-qualified configuration:** ThinkingCap FP8 on pinned
      vLLM `0.23.1rc1.dev531`, FP8 KV, 262,144 served tokens, and five maximum
      sequences. Its validated rollback-control recipe disables MTP; the
      comparison campaign also measured a separate MTP3 lane.
    - **Measured hardware:** One RTX PRO 6000 on Fakoli Dark, one candidate at
      a time.
    - **Evidence:** Functional preflight, tools, a 131K needle, five independent
      8K sessions, repeated ARC and MMLU-Pro slices, and protocol comparisons.
    - **Decision:** Every 27B variant remains `no-promotion`; ThinkingCap is a
      historical quality control, not the immediate rollback.
    - **Important limitation:** The matched 1K completion budget starved
      several candidates, calibrated budgets are not directly comparable, and
      the raw preflight and kernel-inspection transcripts were not retained.
    - **Review dates:** Retained evidence cutoff: 2026-07-13. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-07-12 — Four-checkpoint comparison

The campaign compared a community NVFP4+MTP checkpoint, official FP8,
Unsloth NVFP4, and ThinkingCap FP8 on the same RTX PRO 6000. All candidates
passed bounded functional and five-session checks. ThinkingCap retained the
strongest repeated quality-slice result in this comparison, so it became the
historical strict-quality control. None of the variants earned promotion.

#### 2026-07-12 — Completion-budget calibration

At matched 1,024-token reasoning headroom, ThinkingCap passed ARC 5/5 stably
and MMLU-Pro 7/10 stably. Several other variants exhausted the budget before a
final answer, so these rows must not be read as general intelligence rankings.
Model-specific calibration improved results, including ThinkingCap's repeated
9/10 stable MMLU-Pro result at 4,096 tokens, but the different headroom budgets
are not directly comparable.

#### 2026-07-13 — Adjacent q36 35B engine experiment

The linked `ambud/q36` record tested a distinct
`unsloth/Qwen3.6-35B-A3B-MTP-GGUF` checkpoint and specialized engine. It is
not one of the four 27B variants, is not load-compatible with ThinkingCap, and
must not be blended into this dossier's 27B comparison or decision.

## Immutable identity

### Qwen3.6 27B checkpoints

| Variant | Repository | Revision |
|---|---|---|
| Community NVFP4+MTP | `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | `6f194695406a3bc88a00573187d5b2eecf984a99` |
| Official FP8 | `Qwen/Qwen3.6-27B-FP8` | `e89b16ebf1988b3d6befa7de50abc2d76f26eb09` |
| Unsloth NVFP4 | `unsloth/Qwen3.6-27B-NVFP4` | `ccdaab7e68af2409599b8949a8f2685703c9bae5` |
| ThinkingCap FP8 | `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8` | `e48255afd77b403446332be0f595868337b36591` |

### Runtime identities

- Community, official, and ThinkingCap image digest:
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- Unsloth vLLM 0.25.0 image digest:
  `sha256:e1c1ff1af9a15921bfa11d1d95047258c1797392cdbfa296e7639da446b23f97`.

## Tested hardware and topology

### Comparison lane

- Host label: Fakoli Dark.
- Hardware: one RTX PRO 6000.
- Isolation: one candidate loaded at a time.
- Admission: up to five sequences in the tested recipes.

Other accelerator products and multi-GPU execution were **not tested**.

## Engine, quantization, KV, context, and concurrency recipe

### Community NVFP4+MTP

- vLLM `0.23.1rc1.dev531`.
- ModelOpt NVFP4, FP8 KV, MTP3.
- 262,144 served tokens; five maximum sequences.

### Official FP8

- vLLM `0.23.1rc1.dev531`.
- FP8 weights, FP8 KV, MTP3.
- 262,144 served tokens; five maximum sequences.

### ThinkingCap FP8

- vLLM `0.23.1rc1.dev531`.
- FP8 weights and FP8 KV.
- 262,144 served tokens; five maximum sequences.
- The validated rollback-control recipe disables MTP. A separate comparison
  lane exercised MTP3; do not combine the two into one claimed recipe.

The public [serve-recipe registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml) retains
the reconstructable ThinkingCap control.

### Unsloth NVFP4

- vLLM 0.25.0.
- Compressed-tensors NVFP4, FP8 KV, MTP2.
- 262,144 served tokens; five maximum sequences.

## Evidence by measurement class

### Functional and context

- Preflight, 128K/131K context needle, and 20/20 shared-prefix tool calls:
  recorded as passes for the compared candidates.
- The preflight transcripts themselves were **not retained** in the staged raw
  evidence, so those deployment facts are not independently recoverable from
  that artifact directory.

### Five-session capacity

All four candidates completed 5/5 independent requests with 8K contexts. This
does not establish five simultaneous full 262K windows.

### Repeated bounded quality

- ThinkingCap at matched 1K headroom: ARC 5/5 stable and 15/15 attempts;
  MMLU-Pro 7/10 stable and 21/30 attempts.
- ThinkingCap at calibrated 4K headroom: MMLU-Pro 9/10 stable and 27/30
  attempts.
- The other calibrated rows used model-specific 4K or 8K budgets and are not
  directly comparable to the matched 1K run or to one another.

### Adjacent experiment boundary

The 2026-07-13 q36 35B result is separate engine/model evidence and is **not
tested** as a substitute configuration for any 27B checkpoint in this dossier.

## Decision and promotion state

### Retained

- ThinkingCap remains the historical strict-quality control.
- Exact checkpoint, MTP, and prefix-caching boundaries must stay pinned to the
  corresponding recipe.

### Not authorized

- All four 27B variants are `no-promotion`.
- This record does not establish a universal model ranking, an immediate
  rollback, or current live state.

## Failures and gotchas

### Evaluation interpretation

- Matched 1K reasoning budgets starved several variants before their final
  answer.
- Model-specific 4K and 8K headroom results are not directly comparable.
- Five 8K sessions do not prove five full 262K sessions or a one-million-token
  operating window; those shapes were **not tested**.

### Evidence retention

- Raw functional preflight transcripts: **Not retained.**
- Unsloth kernel-inspection and startup-log transcripts: **Not retained.**
- Community checkpoint identity must remain pinned to its exact revision.

### Adjacent q36 engine

The q36 35B experiment documented a byte-identity discrepancy between MTP on
and off even though the engine described it as lossless. That issue belongs to
the separate 35B engine record and does not change the 27B decision.

## Dated run history

- [2026-07-13 adjacent q36 35B container recipe](../../findings/2026-07-13-q36-pro6000-container-recipe.md)
- [2026-07-12 variation bakeoff](../../findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md)
- [2026-07-12 protocol-v2 comparison](../../findings/2026-07-12-qwen36-protocol-v2-comparison.md)
- [2026-07-12 baseline](../../findings/2026-07-12-qwen36-27b-eval-baseline.md)
- [2026-07-12 ThinkingCap record](../../findings/2026-07-12-thinkingcap-heavy-promotion.md)
