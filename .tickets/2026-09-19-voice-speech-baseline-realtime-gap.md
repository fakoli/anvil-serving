# Speech-baseline benchmark does not measure Realtime or acoustic playback

**Status:** Open

## Current boundary

`anvil-serving voice benchmark --scope end-to-end --input-wav ...
--reference-text ...` accepts one bounded, local PCM16 mono 16-kHz WAV and
records a byte-derived input identity. It replays the existing serialized
STT → LLM → TTS stage calls. This gives a meaningful single-utterance STT WER
sample and endpoint-path latency evidence when the input is recorded speech.

The command does not feed audio through a Realtime session, capture emitted
audio through an output device, or measure acoustic playback. `ttfa_ms` is
therefore time to the first nonempty TTS chunk yielded after any stage-side
buffering in the serialized replay. It is `null` when no audio is yielded, and
is neither a first-response-byte nor an audible time-to-first-audio claim.

## Follow-up

Design a separately bounded, explicitly opt-in Realtime/acoustic measurement
path with a declared capture/playback device contract, calibration method,
privacy handling for recordings, and independent evidence criteria. Keep it
separate from the current endpoint-client benchmark and from LLM promotion
evidence.
