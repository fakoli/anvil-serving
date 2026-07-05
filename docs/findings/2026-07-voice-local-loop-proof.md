# Voice local loop proof: mic -> VAD -> STT -> anvil LLM -> TTS -> speakers

> **STATUS: NOT YET EXECUTED.** This is a measurement-template skeleton for
> anvil task T010 (`scripts/voice/local_loop_demo.py`). No session in the log
> table below is real; every row is a placeholder. Run the script on a
> machine with a real microphone/speaker pair (fakoli-dark or a Mini) with
> the STT/TTS serves and the anvil router all up, and let it append a real
> row via `--capture`.

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
  --config examples/voice/voice.example.toml \
  --duration 60 --capture /tmp/local-loop-run1
```

## Session log

| timestamp (UTC) | turns completed | avg TTFA (ms) | recording | latency JSON |
|---|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

(`local_loop_demo.py --capture PREFIX` appends a row here automatically —
see `append_finding_row` in that script.)

## Findings

_TBD once run — in particular: does `SimpleEnergyVADModel`'s fixed
`--vad-threshold` hold up across a real room's noise floor, or does it need
per-environment calibration (or an adaptive threshold) before this is usable
outside a quiet room?_

## Next steps

_TBD — likely candidates: swap in a real Silero/onnxruntime VAD model behind
`VADModel`; add barge-in energy hysteresis so the assistant's own played-back
audio doesn't self-trigger; measure with a real headset (no AEC needed) vs
open mic/speaker (AEC gap exposed) as two separate rows._
