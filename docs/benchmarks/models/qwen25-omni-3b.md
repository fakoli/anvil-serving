# Qwen2.5-Omni 3B

## Current status and review date

Operator-selectable co-resident stack; `challenger`, `no-promotion`. Review
date: 2026-07-28.

## Immutable identity

`Qwen/Qwen2.5-Omni-3B` revision
`f75b40e3da2003cdd6e1829b1f420ca70797c34e`, image
`anvil-vllm:omni-small-audio-a65f93fb2`.

## Tested hardware and topology

RTX 5090 on Fakoli Dark, co-resident with Parakeet and Kokoro. The PRO 6000 was
not measured.

## Engine, quantization, KV, context, and concurrency recipe

Pinned vLLM nightly-derived image with pinned audio packages; 24,576 MiB
reservation plus 2,048 MiB each for STT and TTS.

## Evidence by measurement class

`functional`, `capacity`: text, JSON, 4K retrieval, image/OCR, basic audio
input, and co-resident voice round trip.

## Decision and promotion state

`challenger`, `no-promotion`; retained as an operator-selectable smaller shape.

## Failures and gotchas

Stock image lacked `vllm[audio]`. The rebuilt path accepted audio but produced
a noisy response; it is not STT qualification.

## Dated run history

- [2026-07-27 small Omni plus voice](../../findings/2026-07-27-omni-voice-stack-qualification.md)
