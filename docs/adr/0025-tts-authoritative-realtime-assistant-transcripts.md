# ADR-0025 — TTS-authoritative Realtime assistant transcripts

- **Status:** Accepted
- **Date:** 2026-07-22
- **Relates to:** GitHub issue #281, ADR-0024, `docs/VOICE.md`

## Context

Anvil Voice already streamed assistant audio but did not expose the text that was spoken. Realtime
clients therefore could not render or persist assistant messages. Copying LLM chunks before TTS is
not authoritative: TTS may select a normalized fallback, fail after partial audio, or produce no
valid audio. A separate observation queue also creates ordering, turn-reset, backpressure, and
buffer-lifetime problems.

The wire must remain additive for audio-only clients, must correlate every transcript event with
the active response and content item, and must never expose audio, credentials, or unbounded text.

## Considered options

1. Fan out LLM chunks before TTS. This is early and simple, but can claim text that was changed or
   never synthesized and requires cross-queue terminal coordination.
2. Transcribe synthesized audio. This is authoritative to the waveform but adds latency, another
   model call, and a self-verification loop with avoidable recognition errors.
3. Emit the exact TTS-selected text after successful synthesis. This uses the existing FIFO and
   reports the text candidate that actually produced the completed audio.

## Decision

The TTS stage emits a `SpokenText` message when a candidate produces its first valid audio chunk;
after audio exists, that candidate can no longer be retried or replaced. If normalization fallback
succeeds, `SpokenText.text` is that fallback text. If synthesis later fails, TTS emits a
content-free `TTSSynthesisFailed` marker, suppresses the transcript terminal, and ends the response
with failed status. Failure before any audio emits only the failure marker.

For responses explicitly requesting both `audio` and `text` through canonical
`output_modalities` or the legacy `modalities` alias, the Realtime service converts `SpokenText`
to incremental `response.output_audio_transcript.delta` events and emits one terminal
`response.output_audio_transcript.done` before `response.done`. Delta and terminal events carry
`response_id`, `item_id`, `output_index`, and `content_index`. Assistant transcript retention is
capped per response; synthesis failure or cap overflow emits one correlated, content-free
`assistant_transcript_unavailable` error and suppresses the transcript terminal. Audio-only
responses emit no assistant transcript events.

## Consequences

- Persisted assistant text describes the successfully synthesized candidate, including fallback
  normalization, rather than merely the LLM's intended text.
- Transcript deltas follow their audio chunks because `AudioOut`, `SpokenText`, and
  `EndOfResponse` share the TTS output FIFO. No observation sideband or terminal wait is needed.
- A TTS stream that fails after partial audio produces audio plus a correlated transcript error and
  a failed response terminal, never a false complete transcript.
- Sentence batching carries an explicit lexical joiner. Word/sentence boundaries retain one space,
  while hard splits inside an unbroken token or CJK text retain no invented whitespace.
- Realtime response state is strict single-flight: a later audio transcription remains queued until
  the current response terminal has been sent, preventing response-id rebinding under backlog.
- Custom TTS stages that emit audio without authoritative `SpokenText` fail the transcript closed.
- The event names remain the existing Anvil/Workbench compatibility surface; the full upstream
  Realtime protocol is not claimed.
