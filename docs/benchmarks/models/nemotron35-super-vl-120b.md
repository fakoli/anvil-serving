# NVIDIA Nemotron 3.5 Super VL 120B-A12B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Unqualified challenger.
    - **Selected or best-qualified configuration:** SGLang 0.5.21, official NVFP4 TP2 scout, 262144 configured tokens, C1.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q GPUs.
    - **Evidence:** functional and bounded context/vision evidence; preflight 6/6, vision 18/18, context 2/2; Oracle 48/54 and C1 0/4 performance-eligible.
    - **Decision:** `no-promotion`; the incumbent was restored with route and client contract unchanged.
    - **Important limitation:** the exact campaign Oracle gate failed and strict C1 capacity had no eligible requests.
    - **Review dates:** evidence through 2026-10-16; dossier reviewed 2026-10-16.

### Review narrative

#### 2026-10-16 — bounded scout stopped

The exact scout passed native preflight, vision, and two context probes, but did not pass the frozen campaign Oracle rule or the strict C1 capacity prerequisite. **Outcome:** `no-promotion`; C4, stability, and performance work were deliberately not run.

## Immutable identity

### Official NVFP4 scout

- **Model:** `nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4@579ffa0b659bc681b89e649fa7c2d5089399602e`; public alias `nemotron35-super-vl-nvfp4-scout`.
- **Runtime:** SGLang 0.5.21, engine revision `e00930c5489053f26d86b179cee0d087f846acbb`, image `lmsysorg/sglang:v0.5.21@sha256:2dbe4c7f53230b09aaa8fa41891c13c51d9d2e253f486a70822aaf08783fff75`.
- **Artifacts:** public-safe configuration record is retained in the [campaign evidence](../../findings/2026-10-16-sglang-qwen-comparison-evidence/nemotron-scout-public-configuration.json).
- **License:** Not recorded in retained evidence.

## Tested hardware and topology

- **Measured:** two RTX PRO 6000 Blackwell Max-Q GPUs, TP2.
- **Protected or co-resident:** Not recorded in retained evidence.
- **Execution mode:** isolated direct scout.
- **Comparability boundary:** no candidate has eligible controlled capacity evidence, so this lane is not a speed comparison.

## Engine, quantization, KV, context, and concurrency recipe

### Official NVFP4 TP2 scout

- **Engine and image:** SGLang 0.5.21 and the immutable image above.
- **Weights and KV:** official ModelOpt mixed NVFP4/FP8; BF16 KV at startup; float16 Mamba state; no speculation.
- **Topology:** TP2 on two measured GPUs.
- **Contract:** 262144 configured tokens, C1; enabled thinking with forced nonempty content.
- **Runtime controls:** zero-effective-swap containment; the private recipe is represented by the public configuration record.

## Evidence by measurement class

### Bounded functional, context, and vision scout

- **Status:** `functional`; completed native artifacts.
- **Measured:** preflight 6/6 with 26 observations, vision 18/18, and context 2/2 at 235978 and 123169 measured prompt tokens.
- **Limits:** these are prerequisites, not coding, capacity, or promotion qualification.
- **Evidence:** [dated finding](../../findings/2026-10-16-sglang-qwen-comparison.md) and [native projections](../../findings/2026-10-16-sglang-qwen-comparison-evidence/native/).

### Oracle, capacity, and coding stop

- **Status:** failed campaign gate / incomplete coding evidence.
- **Measured:** Oracle was 16/18 in each of three rounds, 48/54 total; C1 was 0/4 performance-eligible after strict validation failures; terminal SWE was 2/5 resolved and 4/5 graded.
- **Limits:** the native 0.75 Oracle floor passed, but the campaign exact 54/54 gate failed. C4 and later capacity/stability gates were not run.
- **Evidence:** [dated finding](../../findings/2026-10-16-sglang-qwen-comparison.md) and retained native projections.

## Decision and promotion state

!!! warning "Promotion remains human-gated"

    This evidence does not authorize a serve, route, or client-catalog change.

### Rejected, superseded, or incomplete

- **Official NVFP4 TP2 scout:** `no-promotion`; it needs a newly authorized configuration that passes the frozen exact correctness and coding gates before any capacity comparison.

## Failures and gotchas

### Evidence and interpretation limits

- **Exact Oracle rule:** 48/54 is not interchangeable with the native per-round 0.75 floor.
- **Capacity:** strict C1 validation failure makes every request performance-ineligible; it is not a zero-throughput measurement.

### Runtime, topology, or integration limits

- **C4:** no C4 result exists; it was stopped after C1 failed.
- **Coding:** the terminal SWE record is incomplete: SymPy/Xarray resolved, Sphinx/Django were unresolved, and Pylint was ungraded after the 60-call limit and an empty patch.

## Dated run history

- 2026-10-16 — [SGLang, Qwen, and Nemotron comparison campaign](../../findings/2026-10-16-sglang-qwen-comparison.md): bounded scout stopped; `no-promotion`.
