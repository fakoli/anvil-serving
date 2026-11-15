# Parakeet TDT 0.6B v3

<!-- benchmark-dossier/v2 -->

## Current status and review date

Latest additional review, 2026-10-06: existing Apple Metal control retained; complete corpus evidence, critical entity gate failed, no new qualification. The older CUDA snapshot below remains historical.

!!! info "Decision snapshot"

    - **Product role:** Published routed-STT reference baseline in the retained
      July 2026 evidence.
    - **Selected or best-qualified configuration:** `parakeet.cpp-server`
      CUDA image at the managed STT endpoint, tested sequentially and at
      concurrency four with normalized 16-kHz mono WAV input.
    - **Measured hardware:** One RTX 5090 on Primary Node; the RTX PRO 6000 was
      protected and left running during the final corpus campaign.
    - **Evidence:** 3.343% primary-human micro-WER, 177.87 ms sequential p95,
      240.43 ms concurrency-four p95, and zero final failures or repetitions.
    - **Decision:** Retain as the published baseline; neither July challenger
      result authorized a route change.
    - **Important limitation:** The model repository is recorded, but its exact
      checkpoint commit is not retained.
    - **Review dates:** Retained evidence cutoff: 2026-07-28. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-07-08 — Initial routed-STT baseline

The earlier STT benchmark established Parakeet as the routed reference used by
the later comparison. That record is historical evidence, not a statement
about unverified live state after the retained evidence cutoff.

#### 2026-07-28 — Multi-sample corpus qualification

The final normalized WAV corpus measured 3.343% primary-human micro-WER with
177.87 ms sequential p95 and 240.43 ms concurrency-four p95. It completed with
zero final failures or repetitions while the RTX PRO 6000 workload remained
protected. Neither challenger result authorized a route change.

## Immutable identity

### Model

- Repository: `nvidia/parakeet-tdt-0.6b-v3`.
- Checkpoint commit: **Not retained.**

### Runtime

- Runtime: `parakeet.cpp-server` CUDA image.
- Retained image ID:
  `sha256:7a08005a8a26dd8fe3709f1df3d8dc44be7afef93a27e0830916cc2f54d0304e`.

## Tested hardware and topology

### Final corpus lane

- Host label: Primary Node.
- Measured device: one RTX 5090.
- Protected co-resident device: one RTX PRO 6000, left running and not used
  for the measured STT candidate.

Other accelerator products were **not tested** by this retained campaign.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified corpus recipe

- Server: `parakeet.cpp-server` CUDA image at the managed STT endpoint.
- Audio preparation: normalized 16-kHz mono WAV.
- Schedules: sequential and concurrency four.
- Quantization, KV cache, and language-model context: **Not applicable** to
  this speech-recognition recipe.

The public
[STT experiment overlay](https://github.com/fakoli/anvil-serving/blob/main/examples/primary-node/stt-experiments/overlays/parakeet-tdt-0.6b-v3.toml)
records the reconstructable service configuration.

## Evidence by measurement class

### Quality

- Primary-human micro-WER: 3.343%.
- Final repetitions: zero.

### Capacity and latency

- Sequential p95: 177.87 ms.
- Concurrency-four p95: 240.43 ms.
- Final request failures: zero.

### Evidence

The dated finding links the retained corpus artifacts and result summaries.
The exact checkpoint commit remains **not retained**.

### 2026-10-06 — Parakeet Metal control

**Status:** contemporaneous Metal control, complete component corpus. **Measured:** canonical human 33/1,077 = 3.064% micro-WER; C1 p50/p95 46.62/81.68 ms (72 requests, 24 distinct cases); C4 p95 279.02 ms (24); supplemental 64/1,864 = 3.433% (120). **Limits:** loaded checkpoint revision not independently attested; critical MISSUS→mister in all three repetitions; microphone/accent/client/soak missing. **Evidence:** [dated finding](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#stt-results) · [hardware](../hardware/apple-m4-max.md#2026-10-06-english-voice-qualification) · [reconstruction](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/reproduction.md).

## Decision and promotion state

### Retained baseline

- Published routed-STT reference in the July 2026 evidence.
- Neither challenger result authorized a route change.

### Current-state boundary

This dossier does not claim that the same route is live after the retained
evidence cutoff.

## Failures and gotchas

### Input-format failure

Initial FLAC requests failed closed and remain retained as incomplete
artifacts. They are not counted in the final corpus result.

### Evidence limitation

The exact checkpoint commit is **not retained**. Reproduction must therefore
distinguish the pinned runtime image from the unpinned model snapshot.

## Dated run history

- 2026-10-06 — [English voice qualification](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#stt-results).

- [2026-07-28 multi-sample qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
- [2026-07-08 earlier STT benchmark](../../findings/2026-07-08-stt-model-benchmark.md)
