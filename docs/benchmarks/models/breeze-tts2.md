# Breeze TTS 2 MLX

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** English native-voice TTS challenger.
    - **Selected or best-qualified configuration:** none qualified; exact tested configurations are documented below.
    - **Measured hardware:** Apple M4 Max, 40-core GPU, 48 GiB unified memory; direct same-host component HTTP.
    - **Evidence:** BF16 resource failure; full q8 component retries and independent ASR proxy.
    - **Decision:** `no-promotion`; keep existing controls without newly qualifying them.
    - **Important limitation:** No human naturalness preference or actual playback acceptance; acoustic ambiguities unresolved.
    - **Review dates:** retained evidence and local documentation review through 2026-10-06 UTC.

### Review narrative

**Role:** English native-voice TTS challenger. **Evidence:** BF16 resource failure; complete q8 streaming and buffered retries. **Decision:** `no-promotion`; no human naturalness winner. The model family is not terminally rejected.

Evidence reviewed: **2026-10-06 UTC**. This is a dated snapshot for an English personal, noncommercial, quality-first assistant, not current live-service reporting.

## Immutable identity

BF16: `mlx-community/Breeze-TTS-2-mlx@3c8829fb7fd335818f085cd2ef49b4100c0e46c8`. q8: `mlx-community/Breeze-TTS-2-mlx-8bit@c6e4a2ff6ab9afba68b7853de802273ffe23fb49`.

Candidate audio runtime: MLX Audio **0.5.8**, source `70f4add32911bab6f869b824864ad9f1e24dcb97`, MLX **0.32.3**. The reviewed Anvil audio launcher file SHA is `e7149c09668713835e1cd70725f2af78608ac023c72541f96462c61d7c924661`; this file hash is not a Git commit. Runtime distribution/source identity and exact local snapshot gates are retained. License/source screening is recorded separately from local qualification in the source registry.

## Tested hardware and topology

Apple M4 Max, 40-core GPU, 48 GiB unified memory. These are direct same-host component HTTP measurements. Private hostnames, addresses, operator identity and paths are redacted. Historical CUDA or other-device evidence is a separate population.

## Engine, quantization, KV, context, and concurrency recipe

Native voice S0; 24-kHz source normalized to 16-kHz PCM. BF16 uses streaming with the 1,200-token HTTP default; q8 explicitly uses 750 tokens, cache zero and worker memory trace. R2 streams; an eight-case diagnostic then full R3 changes only request streaming to false. C1, 60 distinct prompts plus one separate cold request. No KV override.

[Exact reconstruction boundaries](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/reproduction.md) · [Hardware view](../hardware/apple-m4-max.md#2026-10-06-english-voice-qualification)

## Evidence by measurement class

**Status:** BF16 59/60 before managed resource abort; R2 and R3 60/60 warm. **Measured:** R2 first PCM p50/p95 1,527.19/1,582.73 ms; buffered R3 5,458.13/17,346.36 ms. R3 ASR proxy micro-WER 7.749%; 435 guard samples, zero observed swap growth, minimum free memory 42%. **Limits:** no human votes; possible unit omission, added “Yeah,” name pronunciation and pauses require acoustic review. Forty-one digital-zero repeat flags are not confirmed spoken loops. **Evidence:** [TTS results](../../findings/2026-10-06-m4-max-voice-quality-qualification.md#tts-results) and [final assessment](../../findings/2026-10-06-m4-max-voice-quality-qualification-evidence/projections/tts-final-candidate-control-assessment.json).

## Decision and promotion state

The exact tested configurations remain unqualified. Keep the existing Kokoro control without calling it best naturalness. Full completion/resource scope cannot replace blind preference and actual client gates.

## Failures and gotchas

Cache zero controls free buffers, not active memory. The BF16 swap-growth cause is not established; retry residual swap differs. Buffered first-audio timing does not establish interactive playback. Lower ASR WER is not an acoustic winner. One private-name case is redacted with original hashes and metrics retained.

Public artifacts are declared sanitized derivatives, with original private SHA/bytes in the projection ledger. Completion or documentation does not authorize a service, route, or client-catalog change.

## Dated run history

- 2026-10-06 — [English voice qualification](../../findings/2026-10-06-m4-max-voice-quality-qualification.md), with native requests, failures, independent audits, and a closed evidence inventory.
