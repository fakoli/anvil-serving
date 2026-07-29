# Qwen3-ASR 0.6B

## Current status and review date

Benchmark-qualified STT `challenger`; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`Qwen/Qwen3-ASR-0.6B` revision
`5eb144179a02acc5e5ba31e748d22b0cf3e303b0`.

## Tested hardware and topology

RTX 5090 on Fakoli Dark through an isolated candidate endpoint. Qwen2.5-Omni
remained co-resident; the RTX PRO 6000 was protected.

## Engine, quantization, KV, context, and concurrency recipe

Pinned candidate image/patch recorded in the STT experiment manifest;
sequential and concurrency-four corpus schedules.

## Evidence by measurement class

`functional`, `quality`, `capacity`: 3.621% primary-human micro-WER,
113.58 ms sequential p95, stable concurrency-four run, zero final failures.

## Decision and promotion state

`challenger`, `no-promotion`. It met the declared non-inferiority margin but
Parakeet remains routed.

## Failures and gotchas

The result does not qualify multilingual behavior, streaming latency,
translation, diarization, or VAD.

## Dated run history

- [2026-07-28 ASR qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
