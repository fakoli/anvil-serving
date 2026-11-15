# DeepSeek V4.1 Flash

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** unqualified real-weight replacement candidate.
    - **Selected or best-qualified configuration:** none.
    - **Measured hardware:** Stage 2 used one RTX PRO 6000 Blackwell Max-Q GPU; Stage 3, Stage 4, and full-role r2/r3/r4 use the two-GPU TP2 configuration.
    - **Evidence:** compatibility-only Stage 2; bounded Stage 3 dummy-path evidence; bounded Stage 4 real-weight basic evidence; full-role r2/r3 resource-policy failures; r4 first-round Oracle8 failure after passing resources.
    - **Decision:** `no-promotion`; no deployed route, qualification, or promotion result.
    - **Important limitation:** bounded agentic failure evidence does not establish full qualification, comparative intelligence, or throughput.
    - **Review dates:** 2026-10-15.

### Review narrative

This dossier is distinct from the historical DeepSeek V4 Flash 0731 dossier.
Stage 2 accepted only a pinned numerical runtime check with no model weights.
Stage 3 retained admissions, metadata, dummy-proxy, dense-mrow, and
custom-all-reduce diagnostic outcomes before r12 passed its bounded dummy-path
gate. Stage 4 then retained a pre-engine checksum failure and a bounded
real-weight basic gate. Full-role r2 and r3 are resource-policy failures for their exact
configurations; r4 passed its resource gate but failed the first required Oracle8 round. See the [replacement evaluation](../../findings/2026-10-15-replacement-evaluation.md).

## Immutable identity

- candidate artifact: `sudoingx/DeepSeek-V4.1-Flash-EXL3-2.7bpw@f7451e9617f675087881b5016fb210a526c7f99c`
- official Engram source: `deepseek-ai/DeepSeek-V4.1-Flash@2cba9e42aa026125f3ed06c6d98c1db82f7ca027`
- runtime image: `sha256:b6db406afb24809ed7b27c7312556487cb7099baf73fd7423183e9d6f7061e4b`

## Tested hardware and topology

Stage 2 is one-GPU numerical runtime evidence. Stage 3 is a TP2 dummy-path
investigation on the dual RTX PRO 6000 pair. Stage 4 and full-role r2/r3/r4 are
real-weight TP2 diagnostics on that pair. The served-model probes and agentic failure do not establish a deployed route,
full qualification, comparative throughput, or promotion result.

## Engine, quantization, KV, context, and concurrency recipe

The bounded Stage 3 profile declares EXL3 2.7 bpw, TP2, C1, 64K context,
text-only input, 512 MiB KV, and `FULL_DECODE_ONLY`. Stage 4 retained the same
64K/C1/text-only scope with real weights. Full-role r2 configured 327680/C4,
eight images, a 2-GiB KV override, and 2048 batch tokens; r3 used the
workload shape with a 1.5-GiB KV setting, and r4 retained it with a distinct
CPU-offload setting. These are configuration facts, not accepted capacity or graph-replay results. The public
[configuration reconstruction](../../findings/2026-10-15-replacement-evaluation/configuration-reconstruction.json)
records the exact public-safe identities.

## Evidence by measurement class

### Numerical runtime compatibility

Stage 2 accepted numerical runtime compatibility only; no model weights were
loaded.

### Dummy-path and real-weight diagnostics

Stage 3 r12 passed the bounded dummy-path gate only. Stage 4 r7 stopped before
engine initialization at candidate-index integrity; corrected r8 passed a
bounded real-weight 64K/C1/image=0 basic gate. Full-role r2 passed startup and
basic non-thinking checks but failed its frozen reserve gate. r3 passed its
bounded basic and model-aware checks, then failed the same reserve gate; r4
passed its resource gate but missed the first required Oracle8 reliability round.
All counts, resource values, and source boundaries are retained in the
[dated finding](../../findings/2026-10-15-replacement-evaluation.md).

## Decision and promotion state

`no-promotion`. No qualification, route, model selection, or client-catalog
change follows.

## Failures and gotchas

Dummy text has no quality meaning. A configured graph mode and capture-loop
progress do not prove a completed graph gate or replay population. An
engine-reported KV token value does not prove usable long context, C4, or
image-eight workload fit. Older GLM, MiMo, and Mistral historical receipts also
lack uniform actual-worker-source execution proof; this limits comparison
provenance without re-scoring any lane.

## Dated run history

- 2026-10-15 — [replacement evaluation](../../findings/2026-10-15-replacement-evaluation.md): Stage 2 compatibility-only; Stage 3 r12 bounded dummy-path pass; Stage 4 r7/r8 bounded real-weight diagnostics; full-role r2/r3 resource-policy failures; r4 first-round Oracle8 failure after passing resources.
