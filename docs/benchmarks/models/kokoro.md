# Kokoro

## Current status and review date

`current` co-resident TTS component in the RTX 5090 voice shape. Review date:
2026-07-28.

## Immutable identity

Served as `kokoro` through the managed TTS endpoint. The retained July
artifacts pin runtime/image identity but do not establish a reusable model
checkpoint commit; do not infer one.

## Tested hardware and topology

RTX 5090 on Fakoli Dark, co-resident with Parakeet and the small Omni stack.
Older Mini-local research is topology history, not the current reference path.

## Engine, quantization, KV, context, and concurrency recipe

Managed Kokoro OpenAI-compatible TTS service. KV/context fields do not apply;
the voice stack reserves 2,048 MiB for TTS.

## Evidence by measurement class

`functional`, `capacity`: co-resident round trip measured 289.27 ms TTS and
RTF 0.1006. Kokoro also generated the bounded synthetic corpus subset for the
ASR comparison; those samples are not primary-human quality evidence.

## Decision and promotion state

`current` in the optional co-resident voice shape; not an LLM or router
promotion.

## Failures and gotchas

Keep synthetic-agent WER separate from primary-human WER. Historical
broadcast-shape errors do not describe the retained Dark result.

## Dated run history

- [2026-07-27 co-resident voice stack](../../findings/2026-07-27-omni-voice-stack-qualification.md)
- [2026-07-28 ASR corpus use](../../findings/2026-07-28-nemotron35-asr-qualification.md)
