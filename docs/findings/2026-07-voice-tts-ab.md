# Voice TTS A/B: Kokoro-82M vs Orpheus-3B vs Qwen3-TTS 1.7B

> **STATUS: NOT YET EXECUTED.** This is a measurement-template skeleton for
> anvil task T009 (`scripts/voice/preflight_tts.py`). No number in this
> document has been measured; every field below is a placeholder. Run the
> script on **fakoli-dark** (sm_120 GPU, real TTS serves reachable) and paste
> its `--report` JSON output into the table.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8 (sm_120
component guidance) · `scripts/voice/preflight_tts.py`

## How to run

```bash
python scripts/voice/preflight_tts.py \
  --text "The quick brown fox jumps over the lazy dog." \
  --capture-dir /tmp/tts-ab-run1 \
  --report docs/findings/tts-ab-run1.json
```

`--capture-dir` saves each candidate's synthesized WAV for the manual quality
pass below. `DEFAULT_CANDIDATES` in the script are placeholders -- override
with `--candidate` for whatever's actually deployed.

## Measurement template (automated)

| candidate | base_url | model | TTFA (ms) | RTF | notes |
|---|---|---|---|---|---|
| kokoro-82m | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| orpheus-3b | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| qwen3-tts-1.7b | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

## Quality pass (manual — TTFA/RTF are NOT a quality proxy)

Listen to each `--capture-dir/<candidate>.wav` and score 1-5 (naturalness,
prosody, artifact rate):

| candidate | naturalness (1-5) | artifacts noted | listener |
|---|---|---|---|
| kokoro-82m | _TBD_ | _TBD_ | _TBD_ |
| orpheus-3b | _TBD_ | _TBD_ | _TBD_ |
| qwen3-tts-1.7b | _TBD_ | _TBD_ | _TBD_ |

## GPU context

_TBD — paste the `gpu` field from the script's `--report` JSON._

## Findings

_TBD once run. Per the review doc's s8 recommendation: Kokoro-82M is the
default (smallest, RTF ~0.04-0.06); Orpheus-3B is the "expressive" upgrade
(needs a thin `/v1/audio/speech` shim over its custom `/api/generate`, per
the review doc s4); Qwen3-TTS via the ggml/`faster-qwen3-tts` backend avoids
the torch CUDA-graph-capture trap the review doc flags for Qwen3's torch
backend. Record the ACTUAL measured numbers, not the expected shape._

## Decision

_TBD — which TTS engine does the shipped `[voice.tts]` example manifest
default to, and why._
