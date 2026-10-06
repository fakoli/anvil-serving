# MiMo v2.6 Flash

<!-- benchmark-dossier/v2 -->

## Current status and review date

**Status:** `not-qualified`, `no-promotion`. **Measured:** pinned `5711b268` on the LIL vLLM image passed direct preflight 25/25 and frozen SWE 4/5. Native agentic results were 15/18 at vendor sampling, 17/18 greedy, and 17/18 thinking-off, below the required 18/18 gate. **Limits:** configured 327,680 tokens, C4, and eight images were not measured as actual context, concurrent capacity, or multimodal acceptance. No performance result exists. **Evidence:** [2026-10-06 scout](../../findings/2026-10-06-mimo-qualification.md) and [sanitized raw artifacts](../../findings/artifacts/2026-10-06-mimo-qualification/README.md). The raw greedy capture shows the model itself emitted one parallel tool call and the parser preserved it; this is not parser-loss evidence. Exact r11 restoration is verified: direct 25/25, routed 7/7, configuration hashes unchanged, and router readmission complete.

!!! info "Decision snapshot"

    - **Product role:** unqualified candidate.
    - **Selected or best-qualified configuration:** none; the latest pinned vLLM no-spec TP2/C4 scout passed protocol and coding checks but failed the agentic floor.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q cards, native Linux.
    - **Evidence:** latest preflight 25/25 and frozen SWE 4/5; agentic 15/18, 17/18, and 17/18 across three tested request policies. The September SGLang failure remains historical evidence.
    - **Decision:** `no-promotion`; GLM retained.
    - **Important limitation:** no policy passed the required 18/18 agentic gate, so full performance, context, C4, modality, and endurance qualification did not run.
    - **Review dates:** latest vLLM scout 2026-10-06; historical SGLang trial 2026-09-21.

### Review narrative

#### 2026-09-21 — bounded V3 correctness failure

The memory-scale remedy made transport health and load pass for the TP2 candidate, but both bounded probes produced no visible answer. Capacity and throughput testing stopped because correctness did not pass. **Outcome:** incomplete candidate, `no-promotion`.

## Immutable identity

- **Latest model:** `XiaomiMiMo/MiMo-V2.6-Flash-RL@5711b268169967567844e1e560e8a3966da959b1`.
- **Latest runtime:** LIL vLLM `23f2a1830f3d34e5587e501bb6c953bab8e7799f`, image digest `ad7b059336e539068fc8b829fee7e7db04b0c3d9c32c455e69056d704619c729`; [public reconstruction](../../findings/artifacts/2026-10-06-mimo-qualification/configs/mimo26-nospec-qualification.toml).
- **Historical model:** `XiaomiMiMo/MiMo-V2.6-Flash-RL@3b38d063180c3e4aed9691fdc735f3d10b266ee4`.
- **Historical runtime:** SGLang v0.5.20, commit `94602c9c2b7cbdb8efd5c52802dac6a1c180089e`, pinned image in the [public reconstruction](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v26-sglang-tp2-c1-327k.public.toml).

## Tested hardware and topology

- **Measured:** two RTX PRO 6000 Blackwell Max-Q cards; TP2 bounded trial.
- **Comparability boundary:** no valid request-level performance or capacity result because correctness failed.

## Engine, quantization, KV, context, and concurrency recipe

The latest vLLM reconstruction specifies official mixed MXFP4/FP8, FP8 KV, TP2/DCP1, no speculation, 327,680 total tokens, C4 admission, and eight images. Those configured limits did not pass full workload qualification.

The historical SGLang reconstruction specifies official mixed MXFP4/FP8, TP2, 327,680 context and C1. Both reconstructions are public documentation, not executable operator recipes.

## Evidence by measurement class

### Quality scout, 2026-10-06

Preflight passed 25/25 and frozen SWE resolved 4/5. Agentic results were 15/18 at vendor sampling, 17/18 greedy, and 17/18 thinking-off, below the required 18/18 floor. Every policy remains `not-qualified`; no comparative performance or full context/capacity/modality result exists. [Finding and native artifacts](../../findings/2026-10-06-mimo-qualification.md).

### Startup compatibility

- **Status:** `compatibility-only` and bounded negative correctness.
- **Measured:** V3 transport health and load passed at 81.41 GiB/rank with `flashinfer_mxfp4`; both temperature-zero and official sampling probes exhausted 9,216 reasoning tokens with zero visible content.
- **Limits:** 91,342 full-KV tokens at default SWA ratio 0.8, not requested 327,680; no capacity, performance, vision, or coding score.
- **Evidence:** [dated finding](../../findings/2026-09-21-mimo-v26-qualification.md), [sampling diagnostic](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v3-sampling-smoke.json), and [recovered OOM transcript](../../findings/2026-09-21-mimo-v26-qualification-evidence/mimo-v2-tool-transcript.txt).

## Decision and promotion state

!!! warning "Promotion remains human-gated"

    No promotion, route, or client change is authorized.

### Rejected, superseded, or incomplete

- **vLLM no-spec TP2/C4:** rejected on the agentic floor despite SWE 4/5; no promotion or speed claim.
- **SGLang TP2 v2:** incomplete after startup OOM; a supported remedy requires new pinned evidence.

## Failures and gotchas

- **Parser:** legacy `--cuda-graph-max-bs` is ambiguous in SGLang v0.5.20.
- **OOM and correctness:** `AudioProjection` was the V2 final failed allocation. V3 `flashinfer_mxfp4` passed transport health and load, then failed correctness; the corrupt-output cause is unknown. Fused-QKV TP handling and FlashInfer are investigation surfaces, not proven causes.
- **vLLM prior:** the 208 GB text-only reference is not a universal TP2 feasibility proof.

## Dated run history

- 2026-10-06 — [Pinned vLLM no-spec TP2 quality scout](../../findings/2026-10-06-mimo-qualification.md), `not-qualified`/`no-promotion`; SWE 4/5, all three agentic policies below 18/18; exact r11 restored.
- 2026-09-21 — [MiMo v2.6 Flash replacement qualification](../../findings/2026-09-21-mimo-v26-qualification.md), `no-promotion`.
