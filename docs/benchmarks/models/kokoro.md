# Kokoro

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** published co-resident TTS component in the retained
      2026-07-28 RTX 5090 voice shape; not a claim about current live state.
    - **Selected or best-qualified configuration:** managed
      OpenAI-compatible Kokoro TTS service with a 2,048 MiB reservation.
    - **Measured hardware:** RTX 5090 on Fakoli Dark, co-resident with Parakeet
      and the small Omni stack.
    - **Evidence:** `functional` and bounded `capacity`; one retained round
      trip measured 289.27 ms TTS and RTF 0.1006.
    - **Decision:** `current` in the dated optional co-resident voice evidence;
      not an LLM or router promotion.
    - **Important limitation:** exact model checkpoint revision is not
      retained, and the synthetic corpus is not primary-human TTS quality
      evidence.
    - **Review dates:** retained evidence through 2026-07-28; dossier-format
      review 2026-08-31.

[Open the retained voice Compose configuration](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.voice-audio.yml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-27 — Co-resident voice round trip

Kokoro served through the managed TTS endpoint as `kokoro`. In the retained
small-Omni plus voice stack, the dedicated Parakeet/Kokoro round trip completed
with 0.0 WER for the bounded utterance: 710.68 ms total, 421.41 ms STT,
289.27 ms TTS, and TTS RTF 0.1006. The measurement establishes a working
co-resident TTS path; it is not a broad voice-quality study.

**Outcome:** retain Kokoro as the dated optional co-resident TTS component.

#### 2026-07-28 — ASR corpus support

Kokoro generated the six bounded synthetic voice-agent phrases used alongside
24 human recordings in the ASR comparison. Those samples cover weather,
timers, ZIP codes, dates/times, extensions, and correction/cancellation, but
they are not primary-human quality evidence for Kokoro.

Older Mini-local research remains topology history and does not describe the
retained Dark result. Historical broadcast-shape errors likewise do not
invalidate this measured co-resident path.

**Outcome:** synthetic-corpus use is supporting provenance, not a TTS quality
qualification or a new deployment decision.

## Immutable identity

- **Served name:** `kokoro`.
- **Service:** managed OpenAI-compatible TTS endpoint.
- **Observed image identity:** local image ID
  `sha256:81a6937f1ea24cd4a729de517385074cb4ee113ed9ba70bde0557ac912451712`
  in the retained 2026-07-28 runtime-state artifact.
- **Checkpoint repository and revision:** Not retained. Do not infer a model
  commit from the served alias.
- **Registry image digest:** Not retained. The public Compose reference
  `ghcr.io/remsky/kokoro-fastapi-gpu:latest-cu128` is mutable and is not an
  immutable reconstruction by itself.

## Tested hardware and topology

- **Measured:** RTX 5090 on Fakoli Dark.
- **Co-resident:** Parakeet STT and the small Omni stack.
- **Reservation:** 2,048 MiB for TTS.
- **Historical-only topology:** Mini-local experiments are not the retained
  reference path described here.

## Engine, quantization, KV, context, and concurrency recipe

### Retained Dark TTS service

- **Runtime:** Kokoro OpenAI-compatible TTS service.
- **Model alias:** `kokoro`.
- **KV and text context:** Not applicable to this TTS endpoint.
- **Concurrency contract:** Not recorded in the retained dossier evidence.
- **Recipe:** [voice Compose service](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.voice-audio.yml)
  and [managed serve manifest](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.voice.toml).

## Evidence by measurement class

### Co-resident TTS path

- **Status:** `functional`, bounded `capacity`.
- **Measured:** 289.27 ms TTS, RTF 0.1006, and 138,082 output bytes inside a
  710.68 ms dedicated voice round trip.
- **Limit:** one bounded round trip is not a distribution or a primary-human
  listening-quality result.
- **Evidence:** [voice-stack finding](../../findings/2026-07-27-omni-voice-stack-qualification.md)
  and [raw round-trip artifact](../../findings/2026-07-27-omni-stack-evidence/qwen25-omni-small-voice-audio.json).

### Synthetic corpus contribution

- **Status:** corpus provenance support, not a separate model qualification.
- **Measured:** six generated agent-style phrases were included in the ASR
  corpus; synthetic results were reported separately from the 24 human cases.
- **Evidence:** [ASR qualification](../../findings/2026-07-28-nemotron35-asr-qualification.md)
  and [retained runtime state](../../findings/2026-07-28-nemotron35-asr-qualification-evidence/runtime-state.json).

## Decision and promotion state

### Retained dated role

- **Kokoro TTS:** `current` in the optional co-resident voice shape published
  through 2026-07-28. This is not a claim about live operator state.

### Promotion boundary

- The TTS result is not an LLM or router promotion and does not authorize a
  topology change.

## Failures and gotchas

### Evidence and interpretation limits

- **Checkpoint identity:** exact model repository and revision are not
  retained, so an exact model rerun cannot be claimed.
- **Quality boundary:** synthetic-agent WER must remain separate from
  primary-human evidence; the retained TTS timing is not a listening test.

### Runtime and topology limits

- **Mutable image reference:** `latest-cu128` can drift; the retained local
  image ID documents the old runtime but is not a portable registry digest.
- **Historical errors:** older broadcast-shape errors and Mini-local research
  do not describe the retained Dark result.

## Dated run history

- [2026-07-27 — co-resident voice stack](../../findings/2026-07-27-omni-voice-stack-qualification.md)
- [2026-07-28 — ASR corpus use](../../findings/2026-07-28-nemotron35-asr-qualification.md)
