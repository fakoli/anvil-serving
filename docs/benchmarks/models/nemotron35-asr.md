# Nemotron 3.5 ASR

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** rejected STT replacement candidate retained as local
      one-shot ASR comparison evidence; never promoted to the routed default.
    - **Selected or best-qualified configuration:** exact Nemotron 3.5 ASR
      checkpoint on Transformers 5.13.0, one-shot OpenAI-compatible serving,
      explicit `en-US`, and sequential plus concurrency-four schedules.
    - **Measured hardware:** one RTX 5090 on Fakoli Dark through an isolated
      endpoint; the RTX PRO 6000 was protected, not benchmarked.
    - **Evidence:** `functional`, `quality`, and `capacity`; all final requests
      completed, with 6.685% primary-human micro-WER and 225.45 ms sequential
      p95.
    - **Decision:** `rejected`; WER regressed 3.343 percentage points from
      Parakeet and exceeded the declared one-point non-inferiority margin.
    - **Important limitation:** native streaming, NIM, multilingual accuracy,
      translation, diarization, and VAD were not tested.
    - **Review dates:** retained evidence through 2026-07-28; dossier-format
      review 2026-08-31.

[Open the retained STT experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.stt-experiment.yml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-27–28 — One-shot ASR qualification

The exact checkpoint ran through a pinned Transformers 5.13.0 one-shot
candidate image. The versioned 30-case English corpus totaled 170.400625
seconds: 24 deterministic LibriSpeech human utterances and six Kokoro-generated
agent phrases. Each model received one cold request, three sequential
repetitions of all 30 cases, and one concurrency-four pass. Nemotron used
explicit `en-US` conditioning, with six additional English automatic-language
probes.

Every final Nemotron request completed without malformed output, crash, or
repetition. Sequential primary-human micro-WER was 6.685%, versus Parakeet's
3.343%; the 3.343-point regression exceeded the declared one-point margin.
Sequential p95 was 225.45 ms, within the 300 ms ceiling but 26.7% slower than
Parakeet. At concurrency four, primary WER remained 6.685%, p95 reached
747.82 ms, and throughput was 8.68 requests/s.

The candidate endpoint never replaced port 30010 or the router audio route.
After removal, protected Parakeet, Kokoro, Omni, Heavy, and router checks
passed, and Parakeet remained the routed default.

**Outcome:** `rejected` under the declared non-inferiority rule; no promotion
or live route changed.

## Immutable identity

- **Model:** `nvidia/nemotron-3.5-asr-streaming-0.6b` revision
  `f3d333391852ba876df169dcc9ba902d25b6ab0b`.
- **Served name:** `nemotron35-asr`.
- **Runtime:** `transformers-serve` / Transformers 5.13.0.
- **Derived image:**
  `sha256:311ee25ead30100674d2885d4b2fdfb5cf13c8e2748bbcc144f98b014a371bf8`.
- **Base image:** PyTorch 2.9.1 CUDA 13.0,
  `sha256:60f22fb80755fd0b470fb47928dbd55816aa9f847edd95cf43c93253507a9ddf`.
- **Corpus:** `stt-corpus/v1`, manifest SHA-256
  `00716e710f2174cd8cecba1d692282cff46453087001570ed3844598813232fb`.

## Tested hardware and topology

- **Measured:** one NVIDIA GeForce RTX 5090 on Fakoli Dark.
- **Execution mode:** isolated, loopback-only candidate endpoint.
- **Protected:** the RTX PRO 6000 and its workload were not benchmarked by this
  result.
- **Co-resident:** Qwen2.5-Omni-3B and the audio services remained running;
  observed whole-GPU totals are topology totals, not isolated model allocation.

## Engine, quantization, KV, context, and concurrency recipe

### One-shot Transformers ASR lane

- **Engine and image:** Transformers 5.13.0 derived image and digest above.
- **Model path:** exact downloaded snapshot at
  `/models/nemotron-3.5-asr-streaming-0.6b`.
- **Quantization, KV, and LLM context:** Not applicable to this retained
  one-shot ASR endpoint.
- **Conditioning:** explicit `en-US` for the main benchmark.
- **Schedules:** three sequential repetitions plus one concurrency-four pass
  after a cold request.
- **Recipe:** [experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.stt-experiment.yml),
  [managed serve manifest](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.stt-experiment.toml),
  and [benchmark overlay](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/stt-experiments/overlays/nemotron35-asr.toml).

## Evidence by measurement class

### Sequential quality and latency

- **Status:** `functional` and `quality`; all requests completed.
- **Measured:** 6.685% primary-human micro-WER, 121.60 ms p50, 225.45 ms p95,
  3.448% synthetic WER, and 7.87 requests/s.
- **Evidence:** [qualification finding](../../findings/2026-07-28-nemotron35-asr-qualification.md)
  and [sequential artifact](../../findings/2026-07-28-nemotron35-asr-qualification-evidence/nemotron-sequential.json).

### Concurrency-four capacity

- **Status:** bounded `capacity`; zero failures or repetition flags.
- **Measured:** 6.685% primary-human micro-WER, 430.78/747.82 ms p50/p95,
  and 8.68 requests/s.
- **Evidence:** [concurrency-four artifact](../../findings/2026-07-28-nemotron35-asr-qualification-evidence/nemotron-concurrency4.json).

### Declared baseline comparison

- **Status:** completed comparison, failed non-inferiority rule.
- **Measured:** Parakeet primary-human micro-WER 3.343% and sequential p95
  177.87 ms; Nemotron regressed 3.343 percentage points and was 26.7% slower
  at sequential p95.
- **Limit:** Kokoro-generated samples are reported separately and do not
  replace the primary-human decision set.

## Decision and promotion state

### Rejected

- **Nemotron 3.5 ASR:** `rejected` for the one-shot routed-STT replacement
  contract because WER exceeded the declared margin.

### Preserved default

- **Parakeet:** remained the routed default; no endpoint, route, or promotion
  changed.

## Failures and gotchas

### Evidence and interpretation limits

- **Capability scope:** native/configurable streaming latency, NIM,
  multilingual accuracy, translation, diarization, VAD, TTS, and general audio
  understanding were not tested.
- **Language probes:** five of six English probes returned `en-US`; this does
  not establish multilingual or reliable automatic language identification.
- **Model name:** “streaming” in the checkpoint name is not local proof of a
  native-streaming path.

### Topology and benchmark limits

- **Whole-GPU totals:** include co-resident services, display, and host
  applications; they are not isolated Nemotron memory use.
- **Synthetic corpus:** synthetic WER is secondary; the decision uses the 24
  primary-human cases.
- **Concurrency:** c4 p95 exceeded the 300 ms sequential ceiling, while the
  rejection itself was already determined by WER.

## Dated run history

- [2026-07-28 — Nemotron 3.5 ASR qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
