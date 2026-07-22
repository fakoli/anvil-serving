# Voice Realtime proof: official `openai` SDK client <-> anvil Realtime server

> **STATUS: LIVE CAPTURED.** The proof harness is
> `scripts/voice/realtime_sdk_client_demo.py`. A passing `--capture` run
> appends a session row below and writes the artifact bundle under the temp
> `anvil-voice-captures/` directory unless an explicit prefix is supplied.

Related: `docs/findings/2026-07-04-hf-speech-to-speech-review.md` s5 (the
Realtime server, "verified with the official OpenAI Python SDK as client") ·
`anvil_serving/voice/realtime/{app,ws,pool,service,events}.py` ·
`scripts/voice/realtime_sdk_client_demo.py`

## What This Proves

The T014 acceptance command:

```bash
python scripts/voice/realtime_sdk_client_demo.py --capture
```

loads the fakoli-dark manifest by default, starts the same package-owned
Realtime server wiring used by `anvil-serving voice run`, connects with the
official `openai` Python SDK Realtime client, sends synthesized PCM speech
through `input_audio_buffer.append`/`commit`, renders live input transcript
events, cancels the first response with `response.cancel`, sends a second
spoken turn, and requires a completed assistant audio response after the
interruption. Capture validation fails if any output for the cancelled response
arrives after the client sends `response.cancel`. Text-enabled runs additionally
require assistant transcript deltas whose concatenation equals one correlated
terminal transcript, with that terminal arriving before `response.done`.

## Known Caveats

1. The default proof uses configured TTS to synthesize the user's two spoken
   inputs. That is intentional: it keeps the acceptance command automated and
   still exercises audio input, STT, LLM, TTS, Realtime protocol events, and
   the official SDK WebSocket path end to end.
2. The server remains a deliberately partial Realtime implementation:
   server-VAD path, no item delete/truncate, no granular content-part
   streaming, and loopback-only unauthenticated default unless
   `realtime_token_env` is configured for non-loopback binds.
3. The proof logs automated audio/transcript/latency evidence. It is not a
   subjective speech-quality review.

## Session Log

| timestamp (UTC) | turn kind | barge-in tested? | transcript(s) | events captured | audio bytes | completed TTFA / latency ms | proof bundle |
|---|---|---|---|---:|---:|---|---|
| 2026-07-06T06:09:48Z | audio/audio | yes | Please count slowly from one to twenty so I can interrupt you.; Interrupting you now, please answer briefly how many countries are in Africa. | 50 | 75800 | 303.35 / 588.02 | C:\Users\sdoum\AppData\Local\Temp\anvil-voice-captures\realtime-sdk-20260706T060944Z.session.json |
| 2026-07-22T18:14:43Z | audio/audio | yes | Please count slowly from 1 to 20, so I can interrupt you.; Interrupting you now, please answer briefly, how many countries are in Africa? | 40 | 41146 | 253.48 / 414.21 | C:\Users\sdoum\AppData\Local\Temp\anvil-voice-captures\realtime-sdk-20260722T181440Z.session.json |

## Decision

T014 is satisfied by the 2026-07-06T06:09:48Z run: the official SDK connected,
audio input produced live transcripts, `response.cancel` interrupted `resp_1`
with no post-cancel output events, and `resp_2` completed with assistant audio.
The 2026-07-22T18:14:43Z run additionally proves issue #281's assistant-text
contract against the live router/STT/TTS topology: `resp_2` streamed the
assistant transcript `Fifty-five.`, the terminal matched its deltas and preceded
`response.done`, 41,146 output-audio bytes were captured, and the harness reported
no acceptance errors. T017 should independently verify the artifact bundle and
delivery branch.
