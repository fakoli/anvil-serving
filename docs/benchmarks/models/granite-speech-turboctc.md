# Granite Speech TurboCTC MLX

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** English STT challenger.
    - **Selected or best-qualified configuration:** none qualified; exact tested configurations are documented below.
    - **Measured hardware:** Apple M4 Max, 40-core GPU, 48 GiB unified memory; direct same-host component HTTP.
    - **Evidence:** complete FP16 corpus quality; separate q8 static compatibility stop.
    - **Decision:** `no-promotion`; keep existing controls without newly qualifying them.
    - **Important limitation:** Critical entity and point-WER regression; q8 incompatibility is not an FP16 quality score.
    - **Review dates:** retained evidence and local documentation review through 2026-10-06 UTC.

### Review narrative

**Role:** English STT challenger. **Evidence:** complete FP16 corpus; separate q8 static compatibility stop. **Decision:** `no-promotion`.

Evidence reviewed: **2026-10-06 UTC**. This is a dated snapshot for an English personal, noncommercial, quality-first assistant, not current live-service reporting.

## Immutable identity

Measured FP16: `iky1e/granite-speech-5.0-470m-turboctc-mlx-fp16@319abff7072204bc6cd30485aac58da6c1216214`. Static q8 stop: `iky1e/granite-speech-5.0-470m-turboctc-mlx-q8@f8911a51b3be9092a71fd0c676b9d8c3b9812035`.

Candidate audio runtime: MLX Audio **0.5.8**, source `70f4add32911bab6f869b824864ad9f1e24dcb97`, MLX **0.32.3**. The reviewed Anvil audio launcher file SHA is `e7149c09668713835e1cd70725f2af78608ac023c72541f96462c61d7c924661`; this file hash is not a Git commit. Runtime distribution/source identity and exact local snapshot gates are retained. License/source screening is recorded separately from local qualification in the source registry.

## Tested hardware and topology

Apple M4 Max, 40-core GPU, 48 GiB unified memory. These are direct same-host component HTTP measurements. Private hostnames, addresses, operator identity and paths are redacted. Historical CUDA or other-device evidence is a separate population.

## Engine, quantization, KV, context, and concurrency recipe

FP16 weights, no language-conditioning override, 16-kHz mono WAV. The same canonical human/synthetic, C1/C4 and holdout schedules are used. KV cache and LLM context are not applicable.

[Exact reconstruction boundaries](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/reproduction.md) · [Hardware view](../hardware/apple-m4-max.md#2026-10-06-english-voice-qualification)

## Evidence by measurement class

**Status:** FP16 requests complete; q8 not loaded. **Measured:** canonical human 36/1,077 = 3.343% micro-WER, p50/p95 24.23/40.16 ms (72 warm C1 requests); holdout 82/1,864 = 4.399%. **Limits:** the critical MISSUS→miss error and point-WER regression fail gates; paired canonical interval −1.017 to +1.846 percentage points overlaps zero. **Evidence:** [STT results](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#stt-results) and [compatibility inspection](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/projections/compatibility-notes.json).

## Decision and promotion state

Fast component latency does not override critical or quality failures. No promotion. Static q8 incompatibility is not an FP16 quality score.

## Failures and gotchas

The pinned q8 configuration declares global group size 128 while relevant quantized inputs use 64; the pinned loader lacks the needed override. No hidden conversion/runtime patch was admitted. Actual client acceptance and microphone/accent coverage remain missing.

Public artifacts are declared sanitized derivatives, with original private SHA/bytes in the projection ledger. Completion or documentation does not authorize a service, route, or client-catalog change.

## Dated run history

- 2026-10-06 — [English voice qualification](../../findings/2026-10-06-m4-max-voice-quality-qualification.md), with native requests, failures, independent audits, and a closed evidence inventory.
