# Qwen3-ASR 0.6B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Benchmark-qualified English one-shot STT challenger.
    - **Selected or best-qualified configuration:** Official Qwen base image
      with vLLM 0.14.0 and the fail-closed official output parser, 16,384-token
      maximum model length, four maximum sequences, and normalized WAV input.
    - **Measured hardware:** One RTX 5090 on Fakoli Dark through an isolated
      endpoint; Qwen2.5-Omni remained co-resident and the RTX PRO 6000 was
      protected.
    - **Evidence:** 3.621% primary-human micro-WER, 113.58 ms sequential p95,
      137.36 ms concurrency-four p95, and zero final failures or repetitions.
    - **Decision:** `challenger`, `no-promotion`; it met the declared
      non-inferiority margin, but the retained record kept Parakeet routed.
    - **Important limitation:** Only English, non-streaming, one-shot
      transcription was qualified; multilingual behavior, streaming,
      translation, diarization, and VAD were not tested.
    - **Review dates:** Retained evidence cutoff: 2026-07-28. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-07-28 — Runtime adaptation and smoke qualification

The first launch failed because the pinned runtime parsed
`CUDA_VISIBLE_DEVICES` as an integer, and the next launch rejected the custom
`qwen3_asr` architecture before the official package registered it. The
managed image retained Docker's device reservation, exposed ordinal `0`
inside the restricted container, imported `qwen_asr` before vLLM, and applied
the official output parser fail-closed. The final endpoint returned valid
one-shot transcripts rather than Qwen's internal provider envelope.

#### 2026-07-28 — Multi-sample corpus qualification

Qwen3-ASR completed the normalized English corpus sequentially and at
concurrency four. Its 3.621% primary-human micro-WER met the declared
non-inferiority margin, with lower p95 latency than the retained Parakeet
baseline in this campaign. The evidence qualified a challenger, not a route
change.

## Immutable identity

### Model

- Checkpoint: `Qwen/Qwen3-ASR-0.6B`.
- Revision: `5eb144179a02acc5e5ba31e748d22b0cf3e303b0`.

### Runtime

- Official base image digest:
  `sha256:fb75b775f089e06e5a1aaebffd421e37505cc630d50c86d889d95ffa45a7e16a`.
- Derived image tag: `anvil-qwen3-asr:official-vllm-0.14.0`.
- Derived image ID:
  `sha256:8bc0588b53f601ed2a8b9da23a89fa3d90f27bef70ea73d2efde2ba8f93ccc1c`.
- Engine: vLLM 0.14.0 with the official Qwen output parser.

## Tested hardware and topology

### Isolated candidate lane

- Host label: Fakoli Dark.
- Measured device: one RTX 5090.
- Candidate endpoint: isolated, loopback-only serve.
- Co-resident workload: Qwen2.5-Omni remained running.
- Protected device: the RTX PRO 6000 was not used by the candidate.

Other accelerator products and multi-GPU execution were **not tested**.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified recipe

- vLLM 0.14.0 with `qwen_asr` imported before server startup.
- Official `qwen_asr.parse_asr_output` applied before returning the OpenAI
  transcription response.
- Maximum model length: 16,384 tokens.
- Maximum sequences: four.
- Corpus schedules: three sequential repetitions and one concurrency-four run.
- Weight quantization and KV dtype: **Not recorded** in the retained dossier
  evidence.

The public [experiment overlay](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/stt-experiments/overlays/qwen3-asr-0.6b.toml)
and [Compose service](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.stt-experiment.yml)
pin the reconstructable candidate configuration.

## Evidence by measurement class

### Functional

- One-shot transcription contract: pass after the fail-closed parser patch.
- Final failures and repetitions: zero.

### Quality

- Primary-human micro-WER: 3.621%.
- Declared non-inferiority margin versus the retained baseline: pass.

### Capacity and latency

- Sequential p50/p95: 67.40/113.58 ms.
- Concurrency-four p50/p95: 83.98/137.36 ms.
- Concurrency-four throughput: 47.25 requests/s.

### Evidence

The [dated finding](../../findings/2026-07-28-nemotron35-asr-qualification.md)
links corpus provenance, sequential and concurrency-four results, startup
identity, and the friction log.

## Decision and promotion state

### Qualified challenger

- Benchmark-qualified for the recorded English, non-streaming, one-shot STT
  contract.
- Met the declared non-inferiority margin.

### Not authorized

- `no-promotion`.
- The retained decision kept Parakeet routed and does not imply current live
  state after the evidence cutoff.

## Failures and gotchas

### Resolved runtime failures

- The first launch failed on GPU-UUID parsing; the managed container retained
  the Docker device reservation and exposed ordinal `0` internally.
- The second launch rejected the custom architecture; importing `qwen_asr`
  before vLLM registered it.
- The default 65,536-token context did not fit beside the protected workload;
  the qualified one-shot recipe pins 16,384.
- Raw provider envelopes were rejected until the official parser produced the
  transcript contract.

### Unqualified behaviors

Multilingual recognition, streaming latency, translation, diarization, and
VAD were **not tested**.

## Dated run history

- [2026-07-28 ASR qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
