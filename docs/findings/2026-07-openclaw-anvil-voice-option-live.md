# OpenClaw Anvil Voice live validation

Date: 2026-07-06

Task: `openclaw-anvil-voice-option:T008`

Result: PASS for the live speech-to-speech goal.

## Evidence files

- `docs/findings/2026-07-openclaw-anvil-voice-option-live.json`
- `docs/findings/2026-07-openclaw-anvil-voice-mini-validation.json`
- `docs/findings/2026-07-openclaw-anvil-voice-realtime-process.json`
- `docs/findings/2026-07-openclaw-anvil-voice-gateway-smoke.json`
- `docs/findings/2026-07-openclaw-anvil-voice-talk-catalog.json`
- `docs/findings/2026-07-openclaw-anvil-voice-talk-config.json`
- `docs/findings/2026-07-openclaw-anvil-voice-plugin-inspect.json`
- `docs/findings/2026-07-openclaw-anvil-voice-gateway-status.json`

## Key proof

- `mini_validation.py` was rerun with externally managed Mini STT/TTS serves and returned `supported`.
- Anvil Voice realtime, STT, TTS, and OpenClaw Gateway loopback ports were open on the Mini.
- STT model probe found `mlx-community/parakeet-tdt-0.6b-v3`; TTS model probe found `mlx-community/Kokoro-82M-bf16`.
- Router route proof reached tier `local`, model `qwen36-27b`, provider `fast-local`, work class `chat-fast`.
- Missing-token route probe returned HTTP `401`.
- Voice benchmark measured `ttfa_ms: 1224.28`, `turn_latency_ms: 1224.68`, `tts_rtf: 0.1004`, and `tts_output_bytes: 76800`.
- T008 OpenClaw Gateway smoke created `talk.session.create` against provider `anvil`, transport `gateway-relay`, brain `agent-consult`, model `fast-local`.
- The smoke streamed synthesized 24 kHz PCM through `talk.session.appendAudio`, produced final transcript `Testing the local voice proof.`, emitted `76764` output audio bytes across `19` frames, and closed cleanly.
- The refreshed provider catalog on the installed Mini exposed both `g711_ulaw:8000` and `pcm16:24000` input/output audio formats for `anvil`.

## Review Dispositions

- Huygens: fixed evidence-gate mismatch by rerunning Mini validation with `lifecycle = "external"`, adding process/listener/log proof, recording exact commands, and refreshing the Gateway smoke.
- Einstein: fixed harness/MCP issues by removing legacy generated plugin defaults while preserving operator overrides, requiring env SecretRefs for private/tailnet realtime URLs, and adding MCP voice passthrough.
- Plato: fixed OpenClaw provider issues by adding G.711 default/Voice Call explicit format, quiet-silence commit threshold, transcript dedupe, URL credential/query rejection, connect timeout, and source catalog brain compatibility.

## Caveat

The live Mini runs installed OpenClaw `2026.6.11`, so its catalog still reports `brains: ["none"]` for Anvil until the OpenClaw source PR is merged and released. The live session path with `brain: "agent-consult"` passed, and the source catalog fix is covered by `src/gateway/server-methods/talk.test.ts`.
