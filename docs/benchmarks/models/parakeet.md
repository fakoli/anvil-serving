# Parakeet TDT 0.6B v3

## Current status and review date

`current` routed STT baseline. Review date: 2026-07-28.

## Immutable identity

`nvidia/parakeet-tdt-0.6b-v3`; retained runtime image ID
`sha256:7a08005a8a26dd8fe3709f1df3d8dc44be7afef93a27e0830916cc2f54d0304e`.
The current evidence records model identity but not a checkpoint commit.

## Tested hardware and topology

RTX 5090 on Fakoli Dark. During the July 28 corpus run the RTX PRO 6000 was
protected and left running.

## Engine, quantization, KV, context, and concurrency recipe

`parakeet.cpp-server` CUDA image at the managed STT endpoint; sequential and
concurrency-four corpus schedules.

## Evidence by measurement class

`quality`, `capacity`: 3.343% primary-human micro-WER, sequential p95
177.87 ms, concurrency-four p95 240.43 ms, zero final failures/repetition.

## Decision and promotion state

`current`; neither challenger result authorized a route change.

## Failures and gotchas

Initial FLAC requests failed closed and remain retained as incomplete artifacts.
The final corpus used normalized 16-kHz mono WAV.

## Dated run history

- [2026-07-28 multi-sample qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
- [2026-07-08 earlier STT benchmark](../../findings/2026-07-08-stt-model-benchmark.md)
