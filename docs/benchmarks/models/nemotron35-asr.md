# Nemotron 3.5 ASR

## Current status and review date

`rejected` under the declared STT non-inferiority rule. Review date:
2026-07-28.

## Immutable identity

`nvidia/nemotron-3.5-asr-streaming-0.6b` revision
`f3d333391852ba876df169dcc9ba902d25b6ab0b`.

## Tested hardware and topology

RTX 5090 on Fakoli Dark through an isolated candidate endpoint. The RTX PRO
6000 was protected, not benchmarked.

## Engine, quantization, KV, context, and concurrency recipe

Pinned Transformers one-shot candidate image and runtime patch; sequential and
concurrency-four corpus schedules.

## Evidence by measurement class

`functional`, `quality`, `capacity`: every final request completed, 6.685%
primary-human micro-WER, 225.45 ms sequential p95.

## Decision and promotion state

`rejected`; WER regressed 3.343 percentage points from Parakeet and exceeded
the declared margin.

## Failures and gotchas

Native streaming, NIM, multilingual accuracy, translation, diarization, and
VAD were not qualified. “Streaming” in the model name is not local proof.

## Dated run history

- [2026-07-28 ASR qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
