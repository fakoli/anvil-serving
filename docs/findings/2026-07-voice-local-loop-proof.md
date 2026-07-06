# Voice local loop proof: mic -> VAD -> STT -> anvil LLM -> TTS -> speakers

> **STATUS: LIVE PROOF BLOCKED ON INPUT ROUTING.** The T010 acceptance command
> has been executed on fakoli-dark, but it exited 1 because the selected input
> device recorded near-digital silence and produced no VAD turns. No successful
> session row has been recorded below. Run the script on a machine with a real
> microphone/speaker pair (fakoli-dark or a Mini) with the STT/TTS serves and
> the anvil router all up, and let it append a real row via `--capture`.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s3
(barge-in/staleness design) · `anvil_serving/voice/connections/local_audio.py`
· `scripts/voice/_real_pipeline.py` · `scripts/voice/local_loop_demo.py`

## Known gaps going in (flagged, not hidden)

1. **`SimpleEnergyVADModel`** (`scripts/voice/_real_pipeline.py`) is an
   RMS-energy threshold, NOT a real acoustic VAD model (no Silero, no
   ML). Expect false turn boundaries (clipped words, coughs/background noise
   read as speech) until a real detector is wired in behind
   `anvil_serving.voice.stages.vad.VADModel`'s protocol seam.
2. **No echo cancellation.** On an open mic/speaker setup (no headset), the
   TTS audio played through the speaker can be picked up by the mic and
   misread as a barge-in. `local_loop_demo.py` clears pending mic input right
   after a turn ends as a partial mitigation, not a fix — a real deployment
   needs AEC upstream of the VAD stage.
3. ~~**`RealVoicePipeline`** duplicates `VoicePipeline`'s wiring.~~ **RESOLVED
   (PUNCH-LIST #2).** `VoicePipeline` gained `stt_config=`/`tts_config=`/
   `vad_model=` seams (queues built before any stage); `RealVoicePipeline`
   is now a thin subclass of `VoicePipeline`, not a second copy of its wiring.

## How to run

```bash
python scripts/voice/local_loop_demo.py \
  --config examples/voice/fakoli-dark.toml \
  --duration 60 --capture
```

`--capture` with no explicit prefix writes:

- `%TEMP%/anvil-voice-captures/local-loop-<timestamp>.input.wav`
- `%TEMP%/anvil-voice-captures/local-loop-<timestamp>.output.wav`
- `%TEMP%/anvil-voice-captures/local-loop-<timestamp>.events.jsonl`
- `%TEMP%/anvil-voice-captures/local-loop-<timestamp>.latency.json`
- `%TEMP%/anvil-voice-captures/local-loop-<timestamp>.session.json`

Set `ANVIL_VOICE_CAPTURE_DIR` or pass an explicit `--capture PREFIX` when a
specific artifact directory is needed. Live audio artifacts are not intended to
be committed.

The command exits 0 only after a playback-interrupting barge-in turn completes
with TTFA, end-to-end latency, non-empty assistant output audio, and a validated
`/v1/route` proof for the configured local tier.

## Session log

| timestamp (UTC) | turns completed | barge-in observed? | avg TTFA (ms) | avg turn latency (ms) | route probe provider | mic recording | assistant recording | session JSON |
|---|---:|---|---:|---:|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

(`local_loop_demo.py --capture [PREFIX]` appends a row here automatically —
see `append_finding_row` in that script.)

## Findings

### 2026-07-06 failed live attempt

Command:

```bash
python scripts/voice/local_loop_demo.py --capture
```

Result: exited 1, as intended, because no playback-interrupting barge-in was
observed. The diagnostic bundle was written under
`%TEMP%/anvil-voice-captures/local-loop-20260706T024851Z.*` and was not added
to the successful session log table.

Evidence from the diagnostic bundle:

- Input WAV: 59.94s, RMS 0, peak 1, no VAD events.
- Output WAV: 0 frames.
- Turns completed: 0.
- Route proof: passed, `provider=fast-local`, `model=qwen36-27b`, `tier=local`.

Interpretation: router/STT/TTS services were reachable and route proof was good,
but the workstation input path selected by `sounddevice` did not deliver audible
speech to the harness. This blocks the live proof until the microphone route is
fixed or an explicit working `--input-device` is provided.

Open validation question once input works: does `SimpleEnergyVADModel`'s fixed
`--vad-threshold` hold up across a real room's noise floor, or does it need
per-environment calibration (or an adaptive threshold) before this is usable
outside a quiet room?

## Next steps

1. Fix the Windows/sounddevice input route so the selected device records
   audible speech above the VAD threshold, then rerun the exact acceptance
   command.
2. If the default device remains silent, rerun with a known-good explicit
   `--input-device` value from `sounddevice.query_devices()`.
3. After a successful proof, likely candidates remain: swap in a real
   Silero/onnxruntime VAD model behind `VADModel`; add barge-in energy
   hysteresis so the assistant's own played-back audio doesn't self-trigger;
   measure with a real headset (no AEC needed) vs open mic/speaker (AEC gap
   exposed) as two separate rows.
