# Hugging Face speech-to-speech through anvil-serving

This recipe wires Hugging Face's `speech-to-speech` project to use anvil-serving as its LLM
backend. `speech-to-speech` keeps ownership of the voice-agent loop: VAD, STT, turn-taking,
Realtime WebSocket, and TTS. anvil-serving sits behind it as the OpenAI-compatible text model
router.

Do not point an OpenAI Realtime client at anvil. Point that client at `speech-to-speech`. Point
`speech-to-speech`'s LLM backend at anvil.

## 1. Start anvil

Use your normal router config. The shipped local-only example exposes the OpenAI Chat Completions
route at `http://127.0.0.1:8000/v1/chat/completions`.

```bash
anvil-serving serve --config configs/example.toml
```

For voice turns, start with the `chat` preset. It maps to the low-latency local tiers in the
default config and avoids the planning profile's local-only deny path.

For true token-by-token voice latency, create a voice-specific copy of the router config and set
this key inside the existing `[router]` table:

```toml
verify_local_min = false
```

Only do this after `anvil-serving preflight` passes for the local chat tier. The default
`verify_local_min = true` keeps a minimal empty/truncated-output safety check on, which can buffer a
local response before `speech-to-speech` sees the first text delta.

## 2. Start speech-to-speech

Install and run `speech-to-speech` in Realtime mode, selecting its `chat-completions` LLM backend
and pointing that backend at anvil:

```bash
speech-to-speech \
  --mode realtime \
  --stt parakeet-tdt \
  --llm_backend chat-completions \
  --tts qwen3 \
  --model_name chat \
  --responses_api_base_url "http://127.0.0.1:8000/v1" \
  --responses_api_api_key "" \
  --responses_api_stream \
  --enable_live_transcription
```

You can render the same command from the checked-in sidecar manifest:

```bash
anvil-serving voice-sidecar command \
  --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
```

If the anvil router is token-authenticated:

```bash
anvil-serving voice-sidecar command \
  --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml \
  --with-auth
```

That renders the same argument shape as:

```bash
speech-to-speech \
  --mode realtime \
  --stt parakeet-tdt \
  --llm_backend chat-completions \
  --tts qwen3 \
  --model_name chat \
  --responses_api_base_url "http://127.0.0.1:8000/v1" \
  --responses_api_api_key "$ANVIL_ROUTER_TOKEN" \
  --responses_api_stream \
  --enable_live_transcription
```

The API key is only the router bearer token in this setup. It is not a cloud provider key unless
your anvil config explicitly opts into a metered cloud tier. Hugging Face `speech-to-speech` accepts
this token as a command argument today; `--with-auth` expands the env var into process argv at
runtime, so use it only on private hosts where process listings, shell history, and Docker metadata
are protected. Prefer the unauthenticated loopback default for single-host development.

For a containerized sidecar, render a Docker Compose service skeleton and replace
`speech-to-speech:local` with the image you build or publish for the Hugging Face runtime:

```bash
anvil-serving voice-sidecar compose \
  --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
```

The generated compose service keeps the Realtime port loopback-published. Host commands use
`base_url = "http://127.0.0.1:8000/v1"`; the compose renderer uses
`container_base_url = "http://host.docker.internal:8000/v1"` so the sidecar container reaches the
host router instead of its own loopback interface. Replace that value with a Docker service DNS name
or private/tailnet host if your router runs elsewhere.

Add `--with-auth` to `voice-sidecar compose` only when the sidecar container must call an
authenticated router. The generated compose file then passes `ANVIL_ROUTER_TOKEN` into the container
and expands it into the `speech-to-speech` process argv, with the same metadata-exposure caveat as
the host command.

## 3. Connect a Realtime client

Connect your Realtime client to `speech-to-speech`, not anvil:

```text
ws://127.0.0.1:8765/v1/realtime
```

Anvil does not implement `/v1/realtime` or proxy audio frames. Its role here is to quality-gate
and route the LLM requests that `speech-to-speech` makes after STT produces text.

## 4. Bridge through OpenClaw Gateway

For OpenClaw deployments, keep the iOS companion paired to OpenClaw Gateway. The Gateway remains the
phone-facing WebSocket contract, and the Gateway can proxy or bridge voice sessions to the
`speech-to-speech` sidecar. The sidecar then calls anvil for text-model turns.

```text
iOS companion
  -> OpenClaw Gateway WebSocket
  -> speech-to-speech Realtime sidecar ws://127.0.0.1:8765/v1/realtime
  -> anvil-serving Chat Completions http://127.0.0.1:8000/v1
  -> local model tiers / optional configured cloud tier
```

If OpenClaw Gateway and the sidecar run on the same machine, use the loopback sidecar URL above. If
OpenClaw Gateway runs on the Fakoli Mini PC and the sidecar runs on another host, publish the
sidecar on a private LAN or tailnet address and point the Gateway at a host-specific URL such as:

```text
ws://voice-sidecar.tailnet.example:8765/v1/realtime
```

The repository includes a non-authoritative bridge sketch at
[`openclaw-gateway.example.toml`](openclaw-gateway.example.toml). Treat the key names as placeholders
until they are mapped to the exact OpenClaw Gateway config surface in the live deployment; the
topology and secret handling are the intended contract.

Validate the sketch before adapting it:

```bash
anvil-serving voice-sidecar validate \
  --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
```

## 5. Validate 16 GB shared-memory targets

On a generic Mac Mini or small PC with 16 GB shared memory, validate the audio sidecar before
moving any LLM weights onto the same machine. For the current Fakoli Mini OpenClaw topology, do
not run STT, TTS, or LLM model serves on Mini during normal validation; its memory is reserved for
OpenClaw Gateway, Anvil Voice Realtime/proxy, Claude Code, and Codex. The default OpenClaw path
should keep audio models on a non-Mini host or behind a Mini proxy, and route the LLM call through
anvil-serving.

Suggested first pass:

- STT: Parakeet TDT 0.6B class.
- LLM: `--model_name chat` through anvil-serving.
- TTS: the smallest Qwen3-TTS variant that passes the live voice demo.

Fully local STT plus TTS plus a small GGUF LLM on a 16 GB box is experimental until measured. Do
not treat that experiment as approval to place models on the Fakoli Mini reference gateway. If you
try it on another host, record the model names, quantization, and whether the sidecar still meets
voice-latency expectations.

Record this checklist for each run:

- Idle memory before starting `speech-to-speech`.
- Memory after STT/TTS model load.
- Startup time until the Realtime sidecar accepts a WebSocket connection.
- First audio response latency for a short voice turn.
- Failure mode if startup or the first voice turn fails.

## Notes

- Use `127.0.0.1` for local URLs on Windows.
- Use `--model_name chat` for the first run. `planning` is intentionally denied on local-only
  configs unless a measured profile says otherwise.
- If you need the voice agent to call tools, keep `--llm_backend chat-completions`; anvil's
  OpenAI relay preserves tool definitions and tool-call history on that path.
- If you want `speech-to-speech` to use its default Responses API backend, anvil would need a
  `/v1/responses` front-door route first. Today the compatible path is `chat-completions`.
