# Fish Audio S2 Pro MLX q8

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** English native-voice TTS challenger.
    - **Selected or best-qualified configuration:** none qualified; exact tested configurations are documented below.
    - **Measured hardware:** Apple M4 Max, 40-core GPU, 48 GiB unified memory; direct same-host component HTTP.
    - **Evidence:** streaming incompatibility and resource-rejected buffered full attempt.
    - **Decision:** `no-promotion`; keep existing controls without newly qualifying them.
    - **Important limitation:** 56/60 completion; no partial-cell ranking or human/client qualification.
    - **Review dates:** retained evidence and local documentation review through 2026-10-06 UTC.

### Review narrative

**Role:** English native-voice TTS challenger. **Evidence:** unsupported streaming probe and resource-rejected buffered attempt. **Decision:** `no-promotion`; partial cells are not ranked.

Evidence reviewed: **2026-10-06 UTC**. This is a dated snapshot for an English personal, noncommercial, quality-first assistant, not current live-service reporting.

## Immutable identity

`mlx-community/fish-audio-s2-pro-8bit@c8d4481b3f7cbfe64d855c8b7cda7739502fc3ff`.

Candidate audio runtime: MLX Audio **0.5.8**, source `70f4add32911bab6f869b824864ad9f1e24dcb97`, MLX **0.32.3**. The reviewed Anvil audio launcher file SHA is `e7149c09668713835e1cd70725f2af78608ac023c72541f96462c61d7c924661`; this file hash is not a Git commit. Runtime distribution/source identity and exact local snapshot gates are retained. License/source screening is recorded separately from local qualification in the source registry.

## Tested hardware and topology

Apple M4 Max, 40-core GPU, 48 GiB unified memory. These are direct same-host component HTTP measurements. Private hostnames, addresses, operator identity and paths are redacted. Historical CUDA or other-device evidence is a separate population.

## Engine, quantization, KV, context, and concurrency recipe

Buffered request, default voice unverified, 1,024-token total cap, cache zero and worker trace. Source 44.1-kHz PCM is normalized to 16 kHz. Native chunk_length=300 remains unchanged; no hidden caller chunking. C1, full 60-prompt attempt plus separate cold request.

[Exact reconstruction boundaries](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/reproduction.md) · [Hardware view](../hardware/apple-m4-max.md#2026-10-06-english-voice-qualification)

## Evidence by measurement class

**Status:** 56/60 warm requests returned; four long-form requests fail at resource abort. **Measured:** 728,099,718 bytes swap growth exceeds the 536,870,912-byte gate; minimum free memory 28%. Successful-subset ASR micro-WER is 9.443%. **Limits:** the aborted generation peak is unknown; the 15,286,476,287-byte completed-generation MLX peak does not bound it. **Evidence:** [TTS results](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#tts-results), [resource assessment](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/projections/fish-8bit-r2-resource-assessment.json) and [streaming negative probe](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/projections/fish-8bit-streaming-negative-preflight.json).

## Decision and promotion state

The exact tested configuration is resource-rejected. No human winner or new deployment. A proposed 512-token cap is unmeasured and may truncate; any retry must rerun the unchanged full 60 and all gates.

## Failures and gotchas

Pinned upstream streaming raises NotImplementedError; the negative HTTP probe returns zero audio, so it is not a speed comparison. Successful 56-request timing/fidelity cannot qualify the full 60. Cache zero does not cap active allocations; listening and physical client acceptance remain missing.

Public artifacts are declared sanitized derivatives, with original private SHA/bytes in the projection ledger. Completion or documentation does not authorize a service, route, or client-catalog change.

## Dated run history

- 2026-10-06 — [English voice qualification](../../findings/2026-10-06-m4-max-voice-quality-qualification.md), with native requests, failures, independent audits, and a closed evidence inventory.
