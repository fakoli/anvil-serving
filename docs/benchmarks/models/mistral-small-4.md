# Mistral Small 4 119B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical low-latency Heavy challenger and retained
      control; not a current recommendation or live route.
    - **Selected or best-qualified configuration:** official Mistral Small 4
      NVFP4 on vLLM nightly, FP8 KV, `TRITON_MLA`, 131,072 tokens, and five
      admitted sequences.
    - **Measured hardware:** one RTX PRO 6000 Blackwell Max-Q card.
    - **Evidence:** `functional`, `capacity`, and bounded `quality`; 131K and
      tools passed, while the repaired 2,048-token reasoning point stabilized
      only 5/10 items on the short MMLU-Pro slice.
    - **Decision:** `no-promotion`; retain as the low-TTFT control.
    - **Important limitation:** the protocol-v2 artifacts have
      `source_recipe=null`; their lineage was bound after execution rather than
      embedded when generated.
    - **Review dates:** retained evidence through 2026-07-12; dossier-format
      review 2026-08-31.

[Open the retained experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-12 — Initial Heavy challenger comparison

Mistral Small 4 ran on one RTX PRO 6000 with vLLM nightly, official NVFP4
weights, FP8 KV, `TRITON_MLA`, Mistral reasoning and tool parsers, text-only
mode, 131,072 context, and five admitted sequences. It passed the normal
preflight, including the 131K needle and 20/20 tools. On the no-prefix-cache
recipe, the 131K request measured 51.90-second TTFT and 52.55 seconds end to
end. Five independent 8K requests completed at both c1 and c5; TTFT/E2E p50
was 0.30/0.58 seconds at c1 and 1.85/2.46 seconds at c5, with 57.82 and
67.04 aggregate output tok/s.

The initial built-in intelligence checks were not stable enough for selection.
Mistral was the lower-latency candidate for short independent prompts, but
Nemotron Super passed the complete built-in gate and the thinking-enabled
tie-break.

**Outcome:** retain Mistral as the low-latency Heavy control with
`no-promotion`.

#### 2026-07-12 — Repaired reasoning-budget protocol

The protocol-v2 rerun separated visible-answer allocation from reasoning
headroom. Mistral moved from 2/5 stable ARC items with effort `none`, to 4/5
with 1,024 tokens of headroom, and 5/5 with 2,048. At the tuned 2,048-token
point, the bounded ten-row MMLU-Pro slice stabilized 5/10 items and passed
14/30 attempts. Low latency alone did not satisfy the quality gate.

The preserved run-lineage record binds the executed suite bytes, model
revision, Compose service, and exact runtime digest after the run. Because the
original protocol-v2 artifacts did not embed suite hashes or source recipes,
that repair is not equivalent to provenance recorded at generation time.

**Outcome:** the repaired slice clarified the reasoning budget but did not
change the `no-promotion` decision.

## Immutable identity

- **Model:** `mistralai/Mistral-Small-4-119B-2603-NVFP4` revision
  `d57a94c74a961e1f9b489b8b3e792923ca29149b`.
- **Served name:** `mistral-small4-119b-a6b-nvfp4`.
- **Runtime:** vLLM `0.23.1rc1.dev531+ga65f93fb2`, image
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- **License:** Apache-2.0, as recorded in the dated candidate-prior table.

## Tested hardware and topology

- **Measured:** one RTX PRO 6000 Blackwell Max-Q card on Fakoli Dark.
- **Execution mode:** isolated single-card endpoint.
- **Admission:** five sequences; prefix caching disabled for the independent
  prompt comparison.
- **Comparability boundary:** the short-output concurrency throughput is not a
  controlled long-generation decode measurement.

## Engine, quantization, KV, context, and concurrency recipe

### Single-card Heavy challenger

- **Engine and image:** vLLM nightly version and exact observed digest above.
- **Weights and KV:** official NVFP4 and FP8 KV.
- **Runtime controls:** `TRITON_MLA`, Mistral reasoning/tool parsers,
  text-only, no prefix caching.
- **Contract:** 131,072 tokens, five sequences, 16,384 max batched tokens.
- **Recipe:** [tracked experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml).

## Evidence by measurement class

### Functional, capacity, and latency

- **Status:** `functional` and `capacity` pass at the served 131K limit.
- **Measured:** preflight and 20/20 tools passed; the 131K request measured
  51.90-second TTFT. Five-session capacity completed 5/5 at both c1 and c5,
  with 0.30/1.85-second TTFT p50 and 57.82/67.04 aggregate tok/s.
- **Evidence:** [challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md).

### Repeated quality slices

- **Status:** bounded `quality`, insufficient for promotion.
- **Measured:** ARC stabilized 5/5 at 2,048 reasoning-headroom tokens;
  MMLU-Pro stabilized 5/10 with 14/30 passing attempts at the same point.
- **Limit:** these are short sanity slices, not full ARC or MMLU-Pro scores.
- **Evidence:** [protocol-v2 finding](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
  and [run-lineage record](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/run-lineage.json).

## Decision and promotion state

### Retained

- **Low-latency control:** `no-promotion`; preserve the exact 131K recipe and
  latency evidence for bounded comparisons.

### Incomplete

- **Quality gate:** 5/10 stable MMLU-Pro items did not satisfy selection.
- **Advertised 256K context:** Not tested locally.

## Failures and gotchas

### Evidence and interpretation limits

- **Protocol lineage:** protocol-v2 artifacts predate embedded suite hashes and
  have `source_recipe=null`; the later lineage binding must stay explicit.
- **Benchmark scope:** short sanity slices and short-output throughput are not
  general capability or controlled decode claims.

### Runtime and request limits

- **Reasoning control:** the initial harness's Qwen-style template kwargs were
  invalid for Mistral; the repaired protocol used the model-appropriate
  `reasoning_effort` path.
- **Quality versus latency:** lower TTFT did not overcome the repeated quality
  gap.

## Dated run history

- [2026-07-12 — Heavy challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md)
- [2026-07-12 — repaired Heavy evaluation protocol v2](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
