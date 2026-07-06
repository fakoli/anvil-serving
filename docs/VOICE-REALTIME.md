# Anvil Voice Realtime

Anvil voice is the in-repo OpenAI Realtime-compatible endpoint for local-first
speech-to-speech turns. The target use case is replacing a server-to-server
OpenAI Realtime voice-agent WebSocket with:

```text
ws://127.0.0.1:8765/v1/realtime
```

The server is not a transparent WebSocket proxy. It terminates Realtime client
events, runs an isolated `VAD -> STT -> LLM -> TTS` pipeline, and streams
Realtime-style events back to the client. The LLM hop uses OpenAI Chat
Completions against the anvil router, so spoken turns inherit the same
work-class routing, local quality gates, and configured fallback policy as text
chat turns.

Official OpenAI docs describe voice-agent Realtime sessions as long-lived
connections on `/v1/realtime`, with WebSocket as the server-to-server transport
and `gpt-realtime-2` as the hosted reasoning voice model:

- https://developers.openai.com/api/docs/guides/realtime
- https://developers.openai.com/api/docs/guides/realtime-websocket
- https://developers.openai.com/api/docs/guides/realtime-models-prompting

Anvil implements the endpoint shape, not the hosted model. The "model" is the
local cascade plus the routed text LLM behind `voice.llm`.

## Run It

Start or verify the configured router, STT serve, and TTS serve, then run the
Realtime server in the foreground:

```bash
anvil-serving voice run --config examples/voice/fakoli-dark.toml
```

The CLI prints the concrete WebSocket target:

```text
voice run: realtime server up at ws://127.0.0.1:8765/v1/realtime (pool size 4)
```

For single-host development, keep `realtime_host = "127.0.0.1"` and no
Realtime token. For LAN or tailnet exposure, set a non-loopback
`realtime_host` and configure `realtime_token_env`; the manifest validator and
WebSocket server both refuse unauthenticated non-loopback binds.

## Client Contract

Point a Realtime WebSocket client at Anvil's endpoint instead of OpenAI's
hosted Realtime endpoint. Keep the client on the voice-agent conversation path:

```text
ws://127.0.0.1:8765/v1/realtime
```

The official OpenAI Python SDK proof in
`scripts/voice/realtime_sdk_client_demo.py` connects with
`client.realtime.connect(...)`, sends audio with
`input_audio_buffer.append` / `input_audio_buffer.commit`, issues
`response.cancel` for barge-in, and requires a completed assistant audio
response after the interruption. The captured proof is recorded in
`docs/findings/2026-07-voice-realtime-proof.md`.

## Configuration Shape

The live manifest has three wire endpoints:

```toml
[voice]
name = "anvil-voice"
realtime_host = "127.0.0.1"
realtime_port = 8765

[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "chat-fast"
stream = true
api_key_env = "ANVIL_ROUTER_TOKEN"

[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "tdt_ctc-110m"

[voice.tts]
base_url = "http://127.0.0.1:30011/v1"
model = "kokoro"
```

Use `examples/voice/voice.example.toml` as the portable reference,
`examples/voice/fakoli-dark.toml` for the current Fakoli Dark live setup, and
`examples/voice/fakoli-mini.toml` for the Mini validation topology.

## What Is Compatible Today

The shipped server supports the subset needed for a spoken exchange with
barge-in:

- RFC 6455 WebSocket handshake and framing via stdlib only.
- `session.created` plus the core session/conversation/response event flow.
- Audio input through `input_audio_buffer.append` and commit handling.
- Live transcript events from the STT stage.
- Assistant audio and transcript deltas from the TTS/LLM stages.
- `response.cancel` cancellation with stale output dropped.
- A bounded pool of isolated single-session pipeline units.
- `/pool` and `/usage` operational routes.

## What Is Not A Drop-In Match Yet

Treat Anvil voice as a compatible replacement target for controlled
server-to-server voice-agent clients, not as the complete hosted Realtime API:

- No WebRTC or SIP front door.
- No `/v1/realtime/translations` translation session.
- Partial event surface: no item delete/truncate, no granular content-part
  streaming, and no full hosted error taxonomy.
- Server-side VAD/turn-taking is Anvil's pipeline behavior, not OpenAI's
  hosted model behavior.
- No native `gpt-realtime-2` reasoning voice model. Reasoning, tool behavior,
  and prompts live in the routed text LLM and `voice.llm.system_prompt`.
- No automatic long-session 128k audio context. Persisted conversation state
  must be implemented above or beside the current pipeline.

## Relationship To The HF Sidecar Example

`examples/huggingface-speech-to-speech/` is now a legacy/alternate topology:
Hugging Face `speech-to-speech` owns `/v1/realtime`, and Anvil is only its
Chat Completions LLM backend. That recipe remains useful for comparing against
the upstream sidecar or for deployments that already use it, but it is not the
native Anvil Realtime replacement path.

For the stated goal, use `anvil-serving voice run`.
