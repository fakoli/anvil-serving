# Voice local loop proof: mic -> VAD -> STT -> anvil LLM -> TTS -> speakers

> **STATUS: LIVE PROOF BLOCKED ON COMPLETED BARGE-IN REPLY.** The T010
> acceptance command now authenticates against the router, completes ordinary
> mic -> STT -> anvil-routed LLM -> TTS -> speaker turns on fakoli-dark, and
> has observed playback-overlapping barge-in. No successful session row has
> been recorded yet because the latest capture hit the duration-cap shutdown
> before an interrupted reply completed with TTFA/latency/output metrics.

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

PowerShell, loading the router token from `~/.env` without printing it:

```powershell
$line = Get-Content -LiteralPath "$HOME\.env" | Where-Object {
  $_ -match '^\s*ANVIL_ROUTER_TOKEN\s*='
} | Select-Object -First 1
if (-not $line) { throw "ANVIL_ROUTER_TOKEN not found in ~/.env" }
$value = ($line -replace '^\s*ANVIL_ROUTER_TOKEN\s*=', '').Trim()
if ($value.StartsWith('"')) {
  $end = $value.IndexOf('"', 1)
  if ($end -lt 1) { throw "unterminated ANVIL_ROUTER_TOKEN quote in ~/.env" }
  $value = $value.Substring(1, $end - 1)
} elseif ($value.StartsWith("'")) {
  $end = $value.IndexOf("'", 1)
  if ($end -lt 1) { throw "unterminated ANVIL_ROUTER_TOKEN quote in ~/.env" }
  $value = $value.Substring(1, $end - 1)
} else {
  $value = ($value -replace '\s+#.*$', '').Trim()
}
if (-not $value) { throw "ANVIL_ROUTER_TOKEN is empty in ~/.env" }
$env:ANVIL_ROUTER_TOKEN = $value
python scripts\voice\local_loop_demo.py --capture
```

Equivalent explicit config form:

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

In capture mode the harness prints a live timing cue:
`assistant audio started; speak over it now for barge-in proof`. Speak while
the assistant voice is audible, then let the interrupted reply complete before
pressing Ctrl+C.

To diagnose input routing before rerunning the proof:

```bash
python scripts/voice/local_loop_demo.py --list-devices
python scripts/voice/local_loop_demo.py --meter-inputs --meter-seconds 0.35
python scripts/voice/local_loop_demo.py --meter-inputs --input-sample-rate 48000 --meter-seconds 0.5
python scripts/voice/local_loop_demo.py --meter-inputs --input-device 6 --meter-seconds 0.35
```

## Session log

| timestamp (UTC) | turns completed | barge-in observed? | avg TTFA (ms) | avg turn latency (ms) | route probe provider | mic recording | assistant recording | session JSON |
|---|---:|---|---:|---:|---|---|---|---|
| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

(`local_loop_demo.py --capture [PREFIX]` appends a row here automatically —
see `append_finding_row` in that script.)

## Findings

### 2026-07-06 playback-barge observed, completion interrupted by shutdown

Command:

```bash
python scripts/voice/local_loop_demo.py --capture
```

Result: the harness observed playback-overlapping barge-in and dropped stale
audio from the interrupted generations, proving the cancellation path fired.
The capture (`local-loop-20260706T043338Z`) still exited 1 because the 60s
duration cap closed the PortAudio output stream while a later barge-in reply
was still playing, so no playback-interrupting turn reached a clean completed
metric row.

Follow-up fix: shutdown now keeps the audio context open while
`shutdown_gracefully()` and the playback thread drain, records playback write
failures as capture events instead of thread-fatal tracebacks, and refuses to
append a successful findings row if playback failed.

### 2026-07-06 auth-fixed, no-barge live attempts

Command:

```bash
python scripts/voice/local_loop_demo.py --capture
```

Result: auth and the full first-turn pipeline worked. The first capture
(`local-loop-20260706T042143Z`) completed one turn with transcript `Testing
one, two, three, testing one, two, three.`, TTFA 1778.27 ms, turn latency
5848.64 ms, 136096 assistant-output bytes, and a passing route proof:
`provider=fast-local`, `model=qwen36-27b`, `tier=local`.

That capture still exited 1 because no second speech onset happened during the
4.253s assistant-output playback window. It is a valid partial proof for
mic/STT/route/LLM/TTS, but not the T010 acceptance proof.

A second capture (`local-loop-20260706T042231Z`) showed the opposite timing
problem: VAD detected additional speech while a response was pending, but
before assistant playback was active, so it was not counted as a
playback-interrupting barge-in. The harness now prints live timing cues and a
more specific failure hint for this case.

### 2026-07-06 router-auth-blocked live attempt

Command:

```bash
python scripts/voice/local_loop_demo.py --capture
```

Result: the microphone/STT path advanced past the prior silent-input blocker:
the pipeline generated a user turn with transcript `Testing, testing, testing,
testing.` The LLM stage then failed on
`http://100.87.34.66:8000/v1/chat/completions` with HTTP 401 Unauthorized.

Interpretation: the router endpoint was reachable and enforcing auth, but the
live shell did not have `ANVIL_ROUTER_TOKEN` loaded. The harness now validates
configured `api_key_env` variables immediately after manifest load, before
opening audio or starting the live pipeline.

Post-fix route validation with `ANVIL_ROUTER_TOKEN` loaded from `~/.env`
returned status 200 and matched the manifest's expected local route:
`provider=fast-local`, `model=qwen36-27b`, `tier=local`. Capture mode now
runs that authenticated route check before opening audio so a malformed or
unauthorized token fails before the microphone loop.

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

Follow-up input-meter diagnostics confirmed the problem across the 16 kHz
PortAudio paths: every openable input device reported RMS about 0.48 with peak
1-2, or zero frames. A targeted check of device 6 (`Microphone (Arctis Nova Pro
Wir`) reported RMS 0.49 and peak 2 against the proof threshold 500. The issue is
therefore outside the router/STT/TTS path and inside Windows/audio-device
routing or mute state.

A follow-up 48 kHz meter sweep added native-rate input support and downsampling
back to the 16 kHz pipeline rate. That made additional WASAPI devices openable,
but still did not expose usable speech: all measured inputs stayed at peak 0-2
and below the proof threshold. Native-rate capture is therefore supported, but
does not by itself solve the workstation's muted/silent input route.

Open validation question once input works: does `SimpleEnergyVADModel`'s fixed
`--vad-threshold` hold up across a real room's noise floor, or does it need
per-environment calibration (or an adaptive threshold) before this is usable
outside a quiet room?

## Next steps

1. Rerun the exact acceptance command, speak one prompt, wait for the
   `assistant audio started` cue or audible TTS, then speak over the assistant
   voice once and let the interrupted reply finish. Avoid repeated follow-up
   interruptions after the first `playback barge-in observed` cue.
2. If the default input route regresses to silence, rerun with a known-good
   explicit `--input-device` value from `sounddevice.query_devices()`.
3. After a successful proof, likely candidates remain: swap in a real
   Silero/onnxruntime VAD model behind `VADModel`; add barge-in energy
   hysteresis so the assistant's own played-back audio doesn't self-trigger;
   measure with a real headset (no AEC needed) vs open mic/speaker (AEC gap
   exposed) as two separate rows.
