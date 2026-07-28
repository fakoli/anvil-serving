# Nemotron 3.5 ASR qualification evidence

This directory contains the bounded, sanitized evidence for the
[2026-07-28 STT qualification](../2026-07-28-nemotron35-asr-qualification.md).
No credential, container log, model weight, LibriSpeech archive, or
machine-local absolute path is published here.

## Corpus

- [`corpus-manifest.jsonl`](corpus-manifest.jsonl) is the selected
  `stt-corpus/v1` manifest: 24 LibriSpeech cases and six generated agent
  phrases, with references, licenses, source identities, and audio SHA-256
  values.
- [`corpus-provenance.json`](corpus-provenance.json) records the OpenSLR
  archive identities and verified MD5 checksums.
- [`synthetic-audio/`](synthetic-audio/) retains the six generated 16-kHz
  mono WAV inputs. The public LibriSpeech audio remains available from
  OpenSLR and is not duplicated here.

The complete corpus has 30 cases, 170.400625 seconds of audio, and manifest
SHA-256
`00716e710f2174cd8cecba1d692282cff46453087001570ed3844598813232fb`.

## Successful runs

- Parakeet:
  [`sequential`](parakeet-sequential.json),
  [`concurrency 4`](parakeet-concurrency4.json)
- Nemotron 3.5 ASR:
  [`sequential`](nemotron-sequential.json),
  [`concurrency 4`](nemotron-concurrency4.json)
- Qwen3-ASR:
  [`sequential`](qwen3-asr-sequential.json),
  [`concurrency 4`](qwen3-asr-concurrency4.json)

Each successful artifact is `stt-benchmark-evidence/v1`, has
`complete=true`, and retains the cold request, every warm transcript, failures,
WER/CER, latency, real-time factor, throughput, identity, and category
aggregates.

[`runtime-state.json`](runtime-state.json) records bounded GPU observations,
protected image identities, post-run functional checks, and the non-promotion
and route-restoration state.

## Fail-closed development evidence

The initial Parakeet attempt proved that accepting a FLAC file in the corpus
contract does not guarantee every endpoint can decode it:
[`sequential failure`](parakeet-flac-sequential.failure.json) and
[`concurrency failure`](parakeet-flac-concurrency4.failure.json). The final
corpus preparation path normalizes selected public samples to 16-kHz mono WAV
through an explicitly selected FFmpeg executable.

Total checked-in evidence is below 5 MiB and each file is below 1 MiB.
