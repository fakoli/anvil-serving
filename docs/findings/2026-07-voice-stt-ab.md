# Voice STT A/B: parakeet.cpp vs vLLM-served Whisper/Qwen3-ASR

> **STATUS: NOT YET EXECUTED.** This is a measurement-template skeleton for
> anvil task T007 (`scripts/voice/preflight_stt.py`). No number in this
> document has been measured; every field below is a placeholder. Run the
> script on **fakoli-dark** (sm_120 GPU, real STT serves reachable) and paste
> its `--report` JSON output into the table.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s8 (sm_120
component guidance) · `scripts/voice/preflight_stt.py`

## How to run

```bash
python scripts/voice/preflight_stt.py \
  --sample /path/to/a/real/utterance.wav \
  --reference-text "the exact words spoken in that utterance" \
  --report docs/findings/stt-ab-run1.json
```

Repeat with `--candidate` entries pointing at whatever STT serves are
actually deployed (parakeet.cpp, a vLLM Whisper/Qwen3-ASR deployment, ...);
the script's `DEFAULT_CANDIDATES` are placeholders, not real endpoints.

## Measurement template

| candidate | base_url | model | latency (ms) | WER | notes |
|---|---|---|---|---|---|
| parakeet.cpp | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| vllm-whisper | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| qwen3-asr (optional 3rd candidate) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |

## GPU context

_TBD — paste the `gpu` field from the script's `--report` JSON (device name +
sm_XX compute capability), or `nvidia-smi` output if `torch` wasn't
installed on the box that ran this._

## Findings

_TBD once run. Expected shape, per the review doc's s8 recommendation:
parakeet.cpp should win on both latency and RAM (no torch, ggml/C++ runtime);
vLLM Whisper/Qwen3-ASR is the "one engine everywhere" fallback if
parakeet.cpp isn't viable for some reason (e.g. language coverage). Record
whichever way it actually comes out — this is a MEASURE, not a
confirm-the-hypothesis exercise._

## Decision

_TBD — which STT engine does the shipped `[voice.stt]` example manifest
default to, and why. If the measured result contradicts the review doc's
recommendation, say so explicitly and update
`docs/findings/2026-07-04-hf-speech-to-speech-review.md`'s table rather than
silently diverging from it._
