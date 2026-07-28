# Nemotron 3.5 ASR qualification on Fakoli Dark

> **Status: executed 2026-07-27 through 2026-07-28; no promotion.**
> Nemotron 3.5 ASR is **not qualified** for the current one-shot Talk contract.
> Qwen3-ASR 0.6B is a **qualified replacement candidate** under this
> qualification's declared margin, but Parakeet remains the routed default.

## Decision

`nvidia/nemotron-3.5-asr-streaming-0.6b` revision
`f3d333391852ba876df169dcc9ba902d25b6ab0b` completed every request without a
malformed response, crash, or repetition. Its primary-human micro-WER was
6.685%, however, compared with Parakeet's 3.343%. That 3.343 percentage-point
regression exceeds the predeclared one-point margin, so the exact tested
Transformers one-shot recipe is **not qualified**. Sequential p95 was 225.45
ms, within the 300 ms ceiling, but 26.7% slower than Parakeet.

`Qwen/Qwen3-ASR-0.6B` revision
`5eb144179a02acc5e5ba31e748d22b0cf3e303b0` produced 3.621% primary-human
micro-WER, only 0.279 points above Parakeet and within the declared
non-inferiority margin. Its 113.58 ms sequential p95 was 36.1% faster than
Parakeet. It therefore meets the alternate candidate rule: at least 20%
latency improvement with non-inferior WER, zero malformed/failed requests, and
p95 below 300 ms. This is a candidate qualification, not authorization to
change the default STT route.

## Test topology and identities

- Host: Fakoli Dark.
- Candidate GPU: NVIDIA GeForce RTX 5090,
  `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1`, 32,607 MiB.
- Protected Heavy GPU: NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation
  Edition, `GPU-d0f446cf-1771-414c-e116-a39138798a8c`, left running.
- Baseline: Parakeet `tdt-0.6b-v3` at `127.0.0.1:30010`, image ID
  `sha256:7a08005a8a26dd8fe3709f1df3d8dc44be7afef93a27e0830916cc2f54d0304e`.
- Nemotron: loopback-only `127.0.0.1:39041`, Transformers 5.13.0,
  derived image ID
  `sha256:311ee25ead30100674d2885d4b2fdfb5cf13c8e2748bbcc144f98b014a371bf8`.
  Its base is PyTorch 2.9.1 CUDA 13.0 at
  `sha256:60f22fb80755fd0b470fb47928dbd55816aa9f847edd95cf43c93253507a9ddf`.
- Qwen3-ASR: loopback-only `127.0.0.1:39042`, official Qwen image base
  `sha256:fb75b775f089e06e5a1aaebffd421e37505cc630d50c86d889d95ffa45a7e16a`
  with vLLM 0.14.0 and the official Qwen output parser, derived image ID
  `sha256:8bc0588b53f601ed2a8b9da23a89fa3d90f27bef70ea73d2efde2ba8f93ccc1c`.
  The one-shot serve used a 16,384-token maximum model length and four maximum
  sequences.

Docker's container-level `nvidia-smi` listed both host GPUs, but the UUID
device request and in-container PyTorch check exposed exactly one CUDA device:
the RTX 5090. Qwen2.5-Omni-3B remained running on that GPU throughout; the
approved pause fallback was not used.

Whole-GPU usage before candidate load was observed between 28,444 and 29,176
MiB. It reached 31,652 MiB with Nemotron loaded and 31,693 MiB with Qwen3-ASR
loaded. These are topology totals including the protected Omni serve, audio
serves, display, and host applications—not isolated model allocations. The
bounded [runtime state](2026-07-28-nemotron35-asr-qualification-evidence/runtime-state.json)
retains those observations and the before/after protected image identities.

## Corpus and method

The versioned `stt-corpus/v1` manifest contains 30 English cases totaling
170.400625 seconds:

- 12 deterministic LibriSpeech `test-clean` utterances;
- 12 deterministic `test-other` utterances; and
- six Kokoro-generated voice-agent phrases for weather, timers, ZIP codes,
  dates/times, extensions, and correction/cancellation.

Selection is deterministic across speakers and short/medium/long durations.
The 24 human recordings are the primary quality set; synthetic results are
reported separately. The OpenSLR archives were verified before extraction:

| Archive | Bytes | Verified MD5 |
|---|---:|---|
| `test-clean.tar.gz` | 346,663,984 | `32fa31d27d2e1cad72775fee3f4849a9` |
| `test-other.tar.gz` | 328,757,843 | `fb5a50374b501bb3bac4815ee91d3135` |

The final manifest SHA-256 is
`00716e710f2174cd8cecba1d692282cff46453087001570ed3844598813232fb`.
The selected public FLAC files were normalized to 16-kHz mono WAV with FFmpeg
8.1.2; the downloaded portable archive SHA-256 was
`db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`.
LibriSpeech attribution remains CC BY 4.0. Selection identity, licenses,
references, and per-audio hashes are in the
[corpus manifest](2026-07-28-nemotron35-asr-qualification-evidence/corpus-manifest.jsonl).

Each model received one cold request, then three sequential repetitions of all
30 cases, then one concurrency-four pass. Nemotron used explicit `en-US`
conditioning. Six additional human cases used automatic language detection;
these probes test only whether this English sample reports an English tag.

## Results

| Model and schedule | Primary micro-WER | Primary latency p50 / p95 | Synthetic WER / p95 | Requests/s | Failures / repetition |
|---|---:|---:|---:|---:|---:|
| Parakeet, three sequential repetitions | 3.343% | 72.35 / 177.87 ms | 0.000% / 71.65 ms | 12.80 | 0 / 0 |
| Parakeet, concurrency 4 | 3.343% | 184.67 / 240.43 ms | 0.000% / 216.01 ms | 21.00 | 0 / 0 |
| Nemotron, three sequential repetitions | 6.685% | 121.60 / 225.45 ms | 3.448% / 127.24 ms | 7.87 | 0 / 0 |
| Nemotron, concurrency 4 | 6.685% | 430.78 / 747.82 ms | 3.448% / 632.56 ms | 8.68 | 0 / 0 |
| Qwen3-ASR, three sequential repetitions | 3.621% | 67.40 / 113.58 ms | 0.000% / 69.27 ms | 14.86 | 0 / 0 |
| Qwen3-ASR, concurrency 4 | 3.621% | 83.98 / 137.36 ms | 0.000% / 67.35 ms | 47.25 | 0 / 0 |

All six artifacts have `complete=true`. The raw case/punctuation-sensitive CER
for LibriSpeech is intentionally high because the canonical references are
uppercase and unpunctuated while all three models emit natural casing and
punctuation. Normalized WER is the content-quality decision metric; raw CER is
retained to expose exact formatting drift. The synthetic set preserves cased,
punctuated references and is more directly interpretable for that field.

Five of Nemotron's six automatic-language probes returned `en-US`; one returned
no language tag. This verifies neither multilingual accuracy nor reliable
automatic language identification outside these six English samples.

Raw evidence:

- [Parakeet sequential](2026-07-28-nemotron35-asr-qualification-evidence/parakeet-sequential.json)
  and [concurrency 4](2026-07-28-nemotron35-asr-qualification-evidence/parakeet-concurrency4.json)
- [Nemotron sequential](2026-07-28-nemotron35-asr-qualification-evidence/nemotron-sequential.json)
  and [concurrency 4](2026-07-28-nemotron35-asr-qualification-evidence/nemotron-concurrency4.json)
- [Qwen3-ASR sequential](2026-07-28-nemotron35-asr-qualification-evidence/qwen3-asr-sequential.json)
  and [concurrency 4](2026-07-28-nemotron35-asr-qualification-evidence/qwen3-asr-concurrency4.json)

## Capability boundary

Locally verified for Nemotron in this work:

- non-streaming, one-shot transcription through
  `/v1/audio/transcriptions`;
- punctuation and capitalization in returned English text;
- explicit `en-US` processor conditioning;
- an English language tag in five of six unconditioned probes;
- sequential and concurrency-four stability, latency, WER/CER, throughput,
  and RTX 5090 resource residency.

Not locally verified: native/configurable streaming latency, NVIDIA NIM,
multilingual accuracy, translation, diarization, VAD, TTS, general
audio-understanding, captioning, call analytics, on-device deployment, or
fine-tuning. NVIDIA's official model card describes prompt conditioning,
automatic language identification, configurable streaming latency, and
fine-tuning support; those source-derived possibilities are not local
qualification evidence. The exact one-shot WER result does not justify a
native-streaming/NIM follow-up as a replacement path, though such a test could
still answer a separate streaming-specialist question.

The dated [source registry](2026-07-28-nemotron35-asr-qualification-evidence/sources.json)
keeps vendor claims separate from local measurements.

## Restoration and reflection

The experiment never replaced port 30010 and never changed the active router
audio route. After candidate removal, the following protected services matched
their pre-run image IDs and passed functional checks:

- Parakeet transcribed the smoke utterance correctly;
- Kokoro produced a 91,612-byte WAV;
- Qwen2.5-Omni-3B returned its expected model identity and a completion;
- Laguna S Heavy returned its expected identity and a completion; and
- the authenticated router returned HTTP 200, the four configured aliases,
  and a successful `llm.primary` completion.

The candidate containers were removed. Omni was never paused, so no Omni
restart was required. The reference Parakeet route remains unchanged.

The run exposed concrete benchmark and runtime friction. The
[friction log](2026-07-28-nemotron35-asr-qualification-evidence/friction-log.md)
records each workaround and the corresponding CLI, image, test, or skill
revision. The main reusable outcome is the canonical
`anvil-serving-stt-benchmark` skill backed by `voice corpus` and
`voice benchmark --scope stt`; lifecycle stays in managed CLI manifests rather
than skill scripts.
