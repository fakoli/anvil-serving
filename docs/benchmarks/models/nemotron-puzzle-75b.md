# Nemotron Puzzle 75B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical Heavy challenger retained for capacity,
      tool, and MTP comparison; not a current recommendation or live route.
    - **Selected or best-qualified configuration:** official Nemotron Puzzle
      75B NVFP4 on vLLM nightly, FP8 KV, MTP 3, 131,072 tokens, and two
      sequences.
    - **Measured hardware:** one RTX PRO 6000 Blackwell Max-Q card.
    - **Evidence:** `functional`, `capacity`, and bounded `quality`; preflight,
      131K retrieval, 20/20 tools, and controlled long-generation MTP A/B
      passed, while the later planning suite scored 0/5.
    - **Decision:** `no-promotion`; retain the measured challenger, but require
      an exact engine pin and broader quality calibration before reconsidering.
    - **Important limitation:** the old Compose used a mutable nightly and did
      not always pass the model revision; short throughput under-generated and
      is not the decode headline.
    - **Review dates:** retained evidence through 2026-07-12; dossier-format
      review 2026-08-31.

[Open the retained experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-10–11 — Bakeoff qualification and MTP A/B

The official checkpoint ran on vLLM nightly with NVFP4 weights, FP8 KV, MTP
with three speculative tokens, 131,072 context, and two sequences. Preflight,
the 128K needle, and 20/20 tools passed. The campaign's controlled
long-generation A/B measured 91.4 tok/s without MTP and 137.0 tok/s with MTP,
a 1.50x local increase. It also matched the Heavy baseline's 2/2 deterministic
intelligence result in that campaign and superseded MiniMax REAP as the
preferred measured challenger.

**Outcome:** strongest measured Heavy candidate in that campaign, retained as
`no-promotion` pending an immutable engine and an operator decision.

#### 2026-07-12 — Throughput and deterministic-planning recheck

The exact checkpoint was reloaded on the same card with observed vLLM
`0.23.1rc1.dev531+ga65f93fb2` and image digest
`sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
Preflight again passed the 128K needle and tools 20/20. A ten-request 8K
benchmark measured 458.93 ms TTFT p50 and 15.22 aggregate tok/s, but produced
only 101 output tokens total; it is not a clean decode measurement and does
not replace the 137.0 tok/s long-generation result.

The externally authored deterministic planning suite scored 0/5, compared
with 1/5 for the Qwen3.5-122B control. Several answers were directionally
sensible, but the exact contract checks were not waived.

**Outcome:** the recheck preserved the capacity/tool result while adding a
clear quality boundary; no production tier or router policy changed.

## Immutable identity

- **Model:** `nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4` revision
  `1d370e47fbc56d1019a471c2339663cdbbb5236f`.
- **Served name:** `nemotron3-puzzle-75b-nvfp4`.
- **Observed runtime:** vLLM `0.23.1rc1.dev531+ga65f93fb2`, image
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- **Stable-release equivalent:** Not qualified.
- **License/use restriction:** Not recorded in the retained dossier evidence.

## Tested hardware and topology

- **Measured:** one RTX PRO 6000 Blackwell Max-Q 96 GB card on Fakoli Dark.
- **Execution mode:** isolated single-card endpoint, two admitted sequences.
- **Observed residency:** approximately 90,588 MiB steady GPU memory during
  the recheck.
- **Comparability boundary:** the short benchmark under-generated; only the
  matched long-generation A/B supports the MTP decode comparison.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified historical lane

- **Engine and image:** observed vLLM version and image digest above.
- **Weights and KV:** NVIDIA NVFP4 MoE and FP8 KV.
- **Speculation:** MTP with three speculative tokens.
- **Runtime controls:** `nemotron_v3` reasoning parser, `qwen3_coder` tool
  parser, float16 Mamba SSM cache.
- **Contract:** 131,072 tokens, two sequences; thinking disabled for the
  retained preflight and benchmarks.
- **Recipe:** [experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml)
  and [registry summary](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml).

An equivalent rerun must override the mutable image tag with the exact digest
and add `--revision 1d370e47fbc56d1019a471c2339663cdbbb5236f`; the historical
Compose alone is not sufficient.

## Evidence by measurement class

### Functional, capacity, and MTP performance

- **Status:** `functional`, `capacity`, and bounded `quality`.
- **Measured:** 131K retrieval and 20/20 tools passed; controlled
  long-generation decode increased from 91.4 to 137.0 tok/s with MTP 3.
- **Evidence:** [Blackwell bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
  and its linked MTP artifacts.

### 2026-07-12 recheck

- **Status:** functional/capacity reconfirmed; quality promotion evidence did
  not improve.
- **Measured:** 10/10 short requests, 458.93/492.91 ms TTFT p50/p95, but only
  101 total output tokens; deterministic planning 0/5.
- **Limits:** short aggregate throughput is not controlled decode, and the
  five-case planning suite is a bounded contract check rather than a general
  capability score.
- **Evidence:** [recheck finding](../../findings/2026-07-12-nemotron-puzzle-recheck.md),
  [throughput artifact](../../findings/2026-07-12-nemotron-puzzle-recheck-evidence/standard-throughput.json),
  and [planning artifact](../../findings/2026-07-12-nemotron-puzzle-recheck-evidence/deterministic-planning-eval.json).

## Decision and promotion state

### Retained

- **Historical challenger:** `no-promotion`; retain the exact observed lane as
  a strong capacity/tool/MTP comparison.

### Incomplete

- **Runtime:** no stable-release engine equivalent was qualified.
- **Quality:** deterministic planning 0/5; broader calibration remains open.
- **Longer context:** model-card contexts above 131K were not tested locally.

## Failures and gotchas

### Evidence and interpretation limits

- **Short-output benchmark:** it under-generated and must not replace the
  controlled long-generation MTP result.
- **Planning suite:** 0/5 is a retained negative result, not a universal model
  quality judgment.
- **FP8 scale warning:** the runtime defaulted an uncalibrated attention scale
  to 1.0, so accuracy claims remain bounded to the executed checks.

### Reproduction limits

- **Mutable image:** the old service defaulted to `nightly`; use the retained
  digest for equivalence.
- **Revision omission:** the historical Compose did not always pass the exact
  model revision; reruns must do so explicitly.
- **Cold start:** the recheck took about ten minutes, including kernel compile
  and warmup.

## Dated run history

- [2026-07-12 — Heavy-candidate recheck](../../findings/2026-07-12-nemotron-puzzle-recheck.md)
- [2026-07-10–11 — Blackwell bakeoff and MTP A/B](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
