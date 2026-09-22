# MiMo v2.6 Flash

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** unqualified candidate.
    - **Selected or best-qualified configuration:** none; SGLang TP2/C1 trial passed transport health and load but failed bounded correctness.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q cards, native Linux.
    - **Evidence:** `compatibility-only` plus bounded negative correctness; no valid visible response.
    - **Decision:** `no-promotion`; GLM retained.
    - **Important limitation:** V3 correctness failed before capacity testing; full KV is 91,342, not requested 327,680.
    - **Review dates:** evidence through 2026-09-21; reviewed 2026-09-21.

### Review narrative

#### 2026-09-21 — bounded V3 correctness failure

The memory-scale remedy made transport health and load pass for the TP2 candidate, but both bounded probes produced no visible answer. Capacity and throughput testing stopped because correctness did not pass. **Outcome:** incomplete candidate, `no-promotion`.

## Immutable identity

- **Model:** `XiaomiMiMo/MiMo-V2.6-Flash-RL@3b38d063180c3e4aed9691fdc735f3d10b266ee4`.
- **Runtime:** SGLang v0.5.20, commit `94602c9c2b7cbdb8efd5c52802dac6a1c180089e`, pinned image in the [public reconstruction](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v26-sglang-tp2-c1-327k.public.toml).

## Tested hardware and topology

- **Measured:** two RTX PRO 6000 Blackwell Max-Q cards; TP2 bounded trial.
- **Comparability boundary:** no valid request-level performance or capacity result because correctness failed.

## Engine, quantization, KV, context, and concurrency recipe

The candidate reconstruction specifies official mixed MXFP4/FP8, TP2, 327,680 context and C1. It is public documentation, not an executable operator recipe.

## Evidence by measurement class

### Startup compatibility

- **Status:** `compatibility-only` and bounded negative correctness.
- **Measured:** V3 transport health and load passed at 81.41 GiB/rank with `flashinfer_mxfp4`; both temperature-zero and official sampling probes exhausted 9,216 reasoning tokens with zero visible content.
- **Limits:** 91,342 full-KV tokens at default SWA ratio 0.8, not requested 327,680; no capacity, performance, vision, or coding score.
- **Evidence:** [dated finding](../../findings/2026-09-21-mimo-v26-qualification.md), [sampling diagnostic](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v3-sampling-smoke.json), and [recovered OOM transcript](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v2-tool-transcript.txt).

## Decision and promotion state

!!! warning "Promotion remains human-gated"

    No promotion, route, or client change is authorized.

### Rejected, superseded, or incomplete

- **SGLang TP2 v2:** incomplete after startup OOM; a supported remedy requires new pinned evidence.

## Failures and gotchas

- **Parser:** legacy `--cuda-graph-max-bs` is ambiguous in SGLang v0.5.20.
- **OOM and correctness:** `AudioProjection` was the V2 final failed allocation. V3 `flashinfer_mxfp4` passed transport health and load, then failed correctness; the corrupt-output cause is unknown. Fused-QKV TP handling and FlashInfer are investigation surfaces, not proven causes.
- **vLLM prior:** the 208 GB text-only reference is not a universal TP2 feasibility proof.

## Dated run history

- 2026-09-21 — [MiMo v2.6 Flash replacement qualification](../../findings/2026-09-21-mimo-v26-qualification.md), `no-promotion`.
