# Qwen3-ASR 1.7B MLX q8

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** English STT challenger.
    - **Selected or best-qualified configuration:** none qualified; exact tested configurations are documented below.
    - **Measured hardware:** Apple M4 Max, 40-core GPU, 48 GiB unified memory; direct same-host component HTTP.
    - **Evidence:** functional and matched corpus quality with paired uncertainty.
    - **Decision:** `no-promotion`; keep existing controls without newly qualifying them.
    - **Important limitation:** Critical entity error; paired uncertainty overlaps zero; microphone/client gates missing.
    - **Review dates:** retained evidence and local documentation review through 2026-10-06 UTC.

### Review narrative

**Role:** English STT challenger. **Evidence:** complete component corpus and paired quality assessment. **Decision:** `no-promotion`; a lower point WER is not an established quality winner.

Evidence reviewed: **2026-10-06 UTC**. This is a dated snapshot for an English personal, noncommercial, quality-first assistant, not current live-service reporting.

## Immutable identity

`mlx-community/Qwen3-ASR-1.7B-8bit@a8379a2e2f9e313c9292cdf1af4055ab56d50d55`.

Candidate audio runtime: MLX Audio **0.5.8**, source `70f4add32911bab6f869b824864ad9f1e24dcb97`, MLX **0.32.3**. The reviewed Anvil audio launcher file SHA is `e7149c09668713835e1cd70725f2af78608ac023c72541f96462c61d7c924661`; this file hash is not a Git commit. Runtime distribution/source identity and exact local snapshot gates are retained. License/source screening is recorded separately from local qualification in the source registry.

## Tested hardware and topology

Apple M4 Max, 40-core GPU, 48 GiB unified memory. These are direct same-host component HTTP measurements. Private hostnames, addresses, operator identity and paths are redacted. Historical CUDA or other-device evidence is a separate population.

## Engine, quantization, KV, context, and concurrency recipe

Community 8-bit conversion, English conditioning, 16-kHz mono WAV. Canonical C1: 24 human cases × 3, with six synthetic cases separate. C4 repeats once; the supplemental 120-human-case holdout repeats once. KV cache and LLM context are not applicable.

[Exact reconstruction boundaries](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/reproduction.md) · [Hardware view](../hardware/apple-m4-max.md#2026-10-06-english-voice-qualification)

## Evidence by measurement class

**Status:** all corpus requests completed. **Measured:** canonical human 30/1,077 edits/words = 2.786% micro-WER; warm C1 p50/p95 165.37/314.25 ms (72 requests). Supplemental 58/1,864 = 3.112%. **Limits:** canonical paired 95% WER-difference interval is −1.770 to +1.084 percentage points, overlapping zero. The critical MISSUS→mister error occurs in all three repetitions. **Evidence:** [STT results](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#stt-results) and [independent assessment](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/projections/stt-independent-assessment.json).

## Decision and promotion state

No replacement is qualified. Keep the existing Parakeet control without newly qualifying it. Its own critical errors do not relax the frozen zero-critical-error gate.

## Failures and gotchas

This 1.7B MLX conversion is separate from the historical 0.6B CUDA dossier. Actual microphone/accent, endpointing, physical interruption and combined stability coverage remain missing. The graph uses only the matched human C1 population; the native synthetic/aggregate metrics remain retained.

Public artifacts are declared sanitized derivatives, with original private SHA/bytes in the projection ledger. Completion or documentation does not authorize a service, route, or client-catalog change.

## Dated run history

- 2026-10-06 — [English voice qualification](../../findings/2026-10-06-m4-max-voice-quality-qualification.md), with native requests, failures, independent audits, and a closed evidence inventory.
