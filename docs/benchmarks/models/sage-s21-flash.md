# Sage S2.1 Flash

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** measured challenger; not selected.
    - **Best bounded recipes:** 64K/C4 native MTP for concurrent natural-output work; 327K/C2 native MTP for long-context work.
    - **Measured hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, TensorFold TP2.
    - **Evidence:** checkpoint custody, functional, image, bounded context, natural-output C2/C4, and single-request long retrieval; no promotion result.
    - **Decision:** `challenger`, `no-promotion`; no route or deployment change.
    - **Important limitation:** eight requests per natural-output cell. Long retrieval used one request at a time up to 259,774 actual input tokens. Central monitoring registration was unavailable before and after the trial.
    - **Review date:** 2026-11-21.

## Immutable identity

- Candidate checkpoint: `Mia-AiLab/Sage-S2.1-Flash-EXL3-4bpw@e272f9573523b7dee7f1a5579c7b2997644d1c87`.
- Base model source: [`internlm/Sage-S2.1-Flash`](https://huggingface.co/internlm/Sage-S2.1-Flash).
- Runtime identity: TensorFold `1.1.9` at `f350cd3ea253a042e205746928271f9d8f71a82d`; public image digest `sha256:2824d665f9334db0fe9868706cd26dc7bd2604974625a2602c74c78cf90b5886`.

## Tested hardware and topology

Two NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition GPUs were measured in TP2. This is bounded direct evidence, not a live-topology or route claim.

## Engine, quantization, KV, context, and concurrency recipe

The candidate artifact declares EXL3 4-bit routed-expert quantization and uses the checkpoint-native template. R1 is 64K/C4, FP8 KV, four slots, no drafts; R2 is 64K/C4 native MTP with unsharded KV; R3 is 327K/C4 draft-off with sharded KV; R4 is 327K/C2 native MTP. Natural cells use temperature 0.6/top-p 0.95, inputs near 7,684 tokens, and non-fixed outputs. Direct image evidence is 12/12 for R1/R2/R4.

## Evidence by measurement class

### Runtime and workload results

**Status:** `measured`, bounded challenger evidence.

**Measured:** startup was 147.6 seconds; direct preflight was 5/5 with 20/20 native tools; native agentic was 6/6 for Sage and 6/6 for the GLM control; bounded C1 context was 12/12 per arm across 8,192 and 32,768 tokens; six image cases passed twice each (12/12); historical limited-quality was 9/9 and historical context 3/3. R2 C4 completed 8/8 at 115.09 aggregate output tok/s (5.465s visible TTFT p50); R4 C2 completed 8/8 at 103.77 aggregate output tok/s (4.140s visible TTFT p50). R3 and R4 each completed four single-request retrievals through 259,774 actual input tokens.

**Checkpoint custody:** the pinned source was verified complete (94 files, 175,723,101,149 bytes, 83 shards, 150,226 indexed tensors). This is artifact integrity only, not a runtime or model-quality measurement; see the [sanitized evidence bundle](../../findings/2026-11-21-sage-s21-flash-qualification-evidence/README.md).

**Limits.** The two-stream timing workload used eight natural outputs per model. GLM reached the first visible answer in 3.603 s versus Sage's 4.140 s. Sage delivered 103.77 aggregate output tok/s versus GLM's 91.85. The strict fixed-output runs had zero eligible samples, and the fresh single-stream GLM timing was excluded for outside activity. Sage R1, R2 and GLM each passed Python affine-cipher and failed Go alphametics. Independent review confirmed the native grades. The original GLM configuration and its authenticated primary route were restored. Generation-bound observer monitoring was unavailable in the pretrial interval and remained unavailable afterward.

**Evidence:** [dated benchmark finding](../../findings/2026-11-21-sage-s21-flash-qualification.md).

## Decision and promotion state

`challenger`, `no-promotion`. No serving or route decision follows from these bounded results.

## Failures and gotchas

The long-context results are ordinary C1 retrieval with short outputs, not a concurrent full-window guarantee. The MTP acceptance result (214/298 proposed tokens across six deterministic prompts) is a tiny diagnostic, not a general completion-rate claim.

## Dated run history

- 2026-11-21 — [TensorFold qualification](../../findings/2026-11-21-sage-s21-flash-qualification.md): four bounded profiles complete; 64K/C4 MTP is the best concurrent Sage lane and 327K/C2 MTP the best long-context Sage lane; `challenger`, `no-promotion`.
