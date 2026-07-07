# Voice Pipeline

`anvil-serving voice` is the local voice-agent runtime built in this repo. It
runs an OpenAI Realtime-compatible WebSocket server and wires a voice cascade:

```text
Realtime client
  -> anvil-serving voice run
  -> VAD
  -> STT endpoint
  -> anvil router / OpenAI Chat Completions endpoint
  -> TTS endpoint
  -> Realtime audio output
```

The intent is to keep voice ownership local while still using anvil-serving's
router for the LLM turn. STT and TTS stay as replaceable out-of-process serves;
the LLM request still goes through the quality-gated router, so voice turns can
use the same local-first, verified, opt-in-cloud policy as text agents.

The voice pipeline is different from `voice-sidecar`. `voice-sidecar` renders
commands or compose manifests for Hugging Face's `speech-to-speech` project,
where that project owns the Realtime server. `anvil-serving voice` owns the
Realtime server and cascade itself.

## Command Summary

| Command | What It Does | What It Does Not Do |
|---|---|---|
| `anvil-serving voice up` | Validates the voice manifest and starts manifest-owned managed/native STT/TTS lifecycle. | Does not start the Realtime WebSocket server or the LLM router. |
| `anvil-serving voice start` | Alias for `voice up`. | Same as `up`. |
| `anvil-serving voice down` | Stops manifest-owned managed/native STT/TTS lifecycle. | Does not stop the LLM router; a foreground `voice run` process stops with Ctrl+C. |
| `anvil-serving voice stop` | Alias for `voice down`. | Same as `down`. |
| `anvil-serving voice run` | Starts the Realtime WebSocket server in the foreground after probing the LLM, STT, and TTS endpoints. | Does not silently continue when required endpoints are unreachable. |
| `anvil-serving voice benchmark` | Runs one configured end-to-end voice turn and prints latency/quality metrics as JSON. | Does not promote routing policy or prove subjective audio quality by itself. |
| `anvil-serving voice profiles` | Lists manifest profiles or validates the resolved manifest for one profile. | Does not mutate lifecycle or start the Realtime server. |
| `anvil-serving voice bridge` | Forwards STT/TTS TCP ports from a private interface to local audio endpoints. | Does not add auth, inspect audio traffic, or replace endpoint/router tokens. |

Manifest-backed commands take `--config <voice.toml>`. If omitted, the shipped
example manifest is used when present. `up`, `down`, `run`, and `benchmark`
also accept `--profile <name>` to apply `[voice.profiles.<name>]` before
validation.

## Why These Commands Exist

Voice has three separate operational concerns:

1. **Audio model lifecycle:** STT and TTS endpoints may be Docker containers,
   native processes on a voice host, or manually managed services.
   `voice up/down` owns only this layer.
2. **Realtime session serving:** `voice run` owns the WebSocket server and
   session pool. It is foreground by design so shutdown and logs are explicit.
3. **Evidence capture:** `voice benchmark` and the hardware validation scripts
   measure whether the configured STT -> LLM -> TTS path is usable on the
   target host.
4. **Private audio bridging:** `voice bridge` exposes same-host STT/TTS
   endpoints on operator-selected private ports when the audio host and
   Realtime/gateway host are different devices.

Keeping those concerns separate avoids a common failure mode: a command that
appears to start "voice" but only starts one part of the pipeline. `voice up`
makes STT/TTS available; `voice run` is the user-facing Realtime server.

## Manifest Shape

A voice manifest has one `[voice]` section and three endpoint sections:

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
history_max_turns = 8
history_max_message_chars = 1200
tool_result_max_chars = 12000

[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "parakeet"
lifecycle = "managed"

[voice.tts]
base_url = "http://127.0.0.1:30011/v1"
model = "kokoro"
lifecycle = "managed"
response_format = "pcm"

[voice.profiles.dark-audio.stt]
base_url = "http://100.87.34.66:30110/v1"
model = "tdt_ctc-110m"
lifecycle = "external"

[voice.profiles.dark-audio.tts]
base_url = "http://100.87.34.66:30111/v1"
model = "kokoro"
lifecycle = "external"
response_format = "pcm"
```

Manifest hygiene follows the rest of the repo:

- Use `127.0.0.1` for same-host URLs.
- Use private tailnet or direct addresses for cross-device endpoint URLs.
- Store credentials in environment variables and reference only the env-var
  name with `*_env` keys.
- Do not embed credentials in URLs.
- A non-loopback `realtime_host` requires `realtime_token_env`.
- Use profiles for repeatable topology switches instead of copying manifests or
  maintaining one-off shell scripts.
- `voice.llm.history_max_turns` controls session-local memory for completed
  user/assistant turns. Set it to `0` for deterministic validation prompts or
  lower it to cap prompt cost; the default is `8`.
- `voice.llm.history_max_message_chars` trims each remembered user or assistant
  message before it is replayed into the next LLM request; the default is
  `1200`.
- `voice.llm.tool_result_max_chars` trims very large realtime tool outputs
  before the continuation LLM request; the default is `12000`.
- `voice.llm.model` remains the manifest-owned Anvil router preset. Realtime
  clients may send `session.model`, but Anvil Voice does not let that field
  override local routing.

For native audio endpoints, add the lifecycle metadata to the STT/TTS section:

```toml
lifecycle = "native"
workdir = "~/code/mlx-audio"
start_command = ".venv/bin/python -m mlx_audio.server --host 127.0.0.1 --port 30010"
pid_file = "/tmp/anvil-voice/stt.pid"
log_file = "/tmp/anvil-voice/stt.log"
ready_timeout = 120.0
stop_timeout = 5.0
```

## STT/TTS Lifecycle Modes

`voice.stt.lifecycle` and `voice.tts.lifecycle` choose what `voice up/down`
can manage.

| Lifecycle | Use When | `voice up` | `voice down` |
|---|---|---|---|
| `managed` | The audio serve is declared in `serves.toml`. | Delegates to the same serve adapter used by `anvil-serving serves`. | Stops the matching serve. |
| `native` | The audio serve is a same-host process, such as MLX Audio on a Mac Mini or laptop. | Starts `start_command` without a shell, writes `pid_file`, logs to `log_file`, and probes `/models`. | Stops the PID it started; if no PID is present but the endpoint is up, uses optional `stop_command`. |
| `external` | Another supervisor or operator owns the process. | Skips lifecycle and reports that it was skipped. | Skips lifecycle and reports that it was skipped. |

Native lifecycle commands are trusted operator manifest content, similar to a
`serves.toml` `up` command. They are parsed with `shlex` and executed as argv
without a shell.

## Multi-Device Expansion

Fakoli Mini and Fakoli Dark are reference devices, not fixed product roles. The
same voice topology can expand to other laptops or hosts when the configured
endpoints are reachable over Tailscale or another private or direct network path.
See [Device topologies](DEVICE-TOPOLOGIES.md) for the broader role model.

Common layouts:

- Voice and audio on one laptop: run `voice up` and `voice run` on that laptop;
  keep STT/TTS `base_url` values on `127.0.0.1`; use `native` or `managed`
  lifecycle there.
- Voice on one laptop, LLM router on another host: keep STT/TTS local to the
  voice laptop and point `[voice.llm].base_url` at the router's private
  tailnet or direct address.
- Voice host separate from audio host: set STT/TTS `base_url` values to the
  remote private addresses and use `lifecycle = "external"` unless `voice up`
  is being run on the audio host itself through local CLI or a controller.
- Audio host exposing loopback-only STT/TTS to a private network: run
  `anvil-serving voice bridge` on the audio host, then point the voice host's
  profile at the bridge ports.

`lifecycle = "native"` is intentionally same-host. It starts the manifest
command on the host where `anvil-serving voice up` runs; it is not a remote
shell transport. For remote lifecycle, run the command on the resource-owning
host or use an anvil-serving controller on that host.

Any service bound beyond loopback needs the appropriate token env var and
private network controls. Tailscale reachability is the transport requirement;
it is not a replacement for router, Realtime, or controller auth.

`voice bridge` defaults to a loopback bind. A non-loopback bind is refused
unless the operator passes `--i-understand-this-exposes-voice-audio`; use that
only on a concrete private/tailnet address. Wildcard binds such as `0.0.0.0`
also require `--allow-wildcard-listen` and should be reserved for cases where a
firewall or tailnet ACL has already scoped exposure to trusted devices.

## Fakoli Mini

The checked-in Mini topology is one concrete example: audio stays local on the
16 GB Mac Mini while the LLM turn routes to the Fakoli Dark router:

- STT: `http://127.0.0.1:30010/v1`
- TTS: `http://127.0.0.1:30011/v1`
- LLM: Fakoli Dark router over the tailnet

The checked-in manifest is `examples/voice/fakoli-mini.toml`. It uses
`lifecycle = "native"` for both audio endpoints and starts MLX Audio with PID
and log files under `/tmp/anvil-voice-mini`.

This manifest is a live reference, not a portable template. For another laptop
or router host, copy it and replace the LLM `base_url`, expected endpoint host,
expected route/model fields, MLX Audio `workdir`, and lifecycle fields for that
device.

Preview the actions:

```bash
anvil-serving voice up --config examples/voice/fakoli-mini.toml --dry-run
anvil-serving voice down --config examples/voice/fakoli-mini.toml --dry-run
```

Run them on the Mini:

```bash
anvil-serving voice up --config examples/voice/fakoli-mini.toml
anvil-serving voice run --config examples/voice/fakoli-mini.toml
```

Stop the audio processes when done:

```bash
anvil-serving voice down --config examples/voice/fakoli-mini.toml
```

`voice run` stays foreground. Stop it with Ctrl+C.

## OpenClaw Anvil Voice Provider

OpenClaw can use Anvil Voice as a speech-to-speech realtime provider. In that
topology the OpenClaw Gateway owns the browser or call audio relay, while
Anvil Voice owns STT, the fast-tier LLM turn, and TTS:

```text
OpenClaw Talk or Voice Call
  -> OpenClaw Gateway realtime provider "anvil"
  -> ws://127.0.0.1:8765/v1/realtime
  -> anvil-serving voice run
  -> STT -> [voice.llm] anvil router -> TTS
```

Use `examples/voice/openclaw-anvil-voice.toml` for the Mini reference layout.
It keeps the Realtime server and MLX Audio endpoints on the Mini loopback and
routes the LLM turn to the Fakoli Dark router over the private address.
It also declares profiles for repeatable switching:

When OpenClaw sends realtime tools in `session.update` or `response.create`,
Anvil Voice forwards them to the Chat Completions LLM request. If the model
emits a function call, Anvil Voice surfaces the standard Realtime
`response.output_item.added`, `response.function_call_arguments.done`, and
`response.output_item.done` events, plus an OpenClaw compatibility
`conversation.item.done` with `item.type = "function_call"`. It waits for
OpenClaw to submit the matching `function_call_output`, then resumes the same
spoken response. This is the path used by OpenClaw's `openclaw_agent_consult`
tool for normal agent tools, memory, workspace context, and
current-information lookups.

- `mini-audio`: Mini-local MLX Audio STT/TTS, with conversational LLM prompt.
- `dark-audio`: Dark-host STT/TTS reached through private bridge ports
  `30110` and `30111`.
- `mini-validation`: Mini-local audio plus the intentional
  `I understand.` validation prompt.

Start the voice side first:

```bash
anvil-serving voice profiles --config examples/voice/openclaw-anvil-voice.toml
anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml --profile mini-audio --dry-run
anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml --profile mini-audio
anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml --profile mini-audio
```

To keep OpenClaw and Realtime on Mini while using STT/TTS on Fakoli Dark, first
make sure Dark's local STT/TTS endpoints are already running and reachable on
the Dark host:

```bash
curl -s -o /dev/null -w "stt %{http_code}\n" http://127.0.0.1:30010/v1/models
curl -s -o /dev/null -w "tts %{http_code}\n" http://127.0.0.1:30011/v1/models
```

For STT, a 404 can still prove the HTTP server is listening; connection refusal
means the local audio endpoint is not up.

Then expose those loopback audio services on private bridge ports from the Dark
host:

```bash
anvil-serving voice bridge \
  --listen-host 100.87.34.66 \
  --stt-listen-port 30110 \
  --stt-target-host 127.0.0.1 \
  --stt-target-port 30010 \
  --tts-listen-port 30111 \
  --tts-target-host 127.0.0.1 \
  --tts-target-port 30011 \
  --i-understand-this-exposes-voice-audio
```

Then run the Mini Realtime server with the Dark audio profile:

```bash
anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio
```

Then render or apply the matching OpenClaw config. The `--voice` flag adds the
Talk realtime block next to the normal anvil model provider config:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --voice \
  --voice-realtime-url ws://127.0.0.1:8765/v1/realtime \
  --voice-consult-model anvil/chat-fast \
  --out ./openclaw.anvil.json
```

The generated Talk config selects the OpenClaw provider id `anvil` and points
it at the Anvil Voice Realtime server. It also pins forced OpenClaw agent
consults to the low-latency `anvil/chat-fast` preset without changing the
session's normal selected model:

```json5
{
  talk: {
    consultModel: "anvil/chat-fast",
    realtime: {
      mode: "realtime",
      transport: "gateway-relay",
      brain: "agent-consult",
      consultRouting: "force-agent-consult",
      provider: "anvil",
      providers: {
        anvil: {
          realtimeUrl: "ws://127.0.0.1:8765/v1/realtime",
          model: "fast-local",
          silenceDurationMs: 200
        }
      }
    }
  }
}
```

`--voice-consult-model` is optional when the router config exposes the
`chat-fast` preset; `harness sync openclaw --voice` selects `anvil/chat-fast`
by default and falls back to `anvil/chat` if the preset is absent. Pass
`--voice-consult-model anvil/chat` to switch the forced consult path back to
the standard chat preset.

Same-host Anvil Voice can omit a realtime token. If the Realtime server binds
to a private/tailnet address, set `voice.realtime_token_env` in the voice
manifest and pass `--voice-api-key-env ANVIL_VOICE_REALTIME_TOKEN` to the
harness sync command. The emitted OpenClaw config references the env var by
name; it does not contain the token value.

## Realtime Server

`voice run` validates the manifest, probes the configured LLM/STT/TTS
endpoints, then binds the Realtime WebSocket server at:

```text
ws://<realtime_host>:<realtime_port>/v1/realtime
```

It refuses to start if required endpoints are unreachable. A 401, 403, 404, or
405 from a probe still proves that something is listening; a connection failure
or 5xx response blocks startup.

Loopback binds may omit `realtime_token_env` for trusted local development.
Non-loopback binds require a bearer token env var in the manifest.

## Benchmark And Validation

Use `voice benchmark` for a quick configured end-to-end sample:

```bash
anvil-serving voice benchmark --config examples/voice/fakoli-mini.toml
```

The JSON output includes first-audio latency, total turn latency, STT WER, TTS
RTF, output byte counts, and the observed STT/LLM text. This is a smoke
measurement, not a promotion gate.

For the 16 GB Mini proof, use the hardware validation harness:

```bash
python scripts/voice/mini_validation.py --report
```

That report adds target-host checks, router auth proof, endpoint model identity
proof, post-benchmark STT/TTS memory attribution, and a verdict. A non-Mini run
is a negative control unless explicitly allowed with `--allow-unsupported`.

## MCP And Controller Operation

Agents and OpenClaw should prefer `voice_manage` for STT/TTS lifecycle:

```json
{
  "action": "up",
  "config": "examples/voice/openclaw-anvil-voice.toml",
  "profile": "mini-audio"
}
```

The first call previews the plan and returns a dry-run command. A live mutation
requires:

```json
{
  "action": "up",
  "config": "examples/voice/openclaw-anvil-voice.toml",
  "profile": "mini-audio",
  "confirm": true,
  "dry_run": false
}
```

This mirrors `serves_manage` and `router_manage`: exact target first, then an
explicit confirmed call.

## Troubleshooting

- `voice up` says lifecycle is external: change the manifest to `managed` or
  `native`, or keep managing that endpoint manually.
- `voice run` refuses to start: check the `/models` endpoint for the LLM, STT,
  and TTS base URLs named in the manifest.
- Native `voice down` reports `ready_but_unmanaged`: the endpoint is answering,
  but no PID file or `stop_command` can identify what to stop.
- Mini STT/TTS logs live under `/tmp/anvil-voice-mini` in the checked-in Mini
  manifest.
- Router auth errors should be fixed by setting the env var named in
  `voice.llm.api_key_env`; do not paste token values into the manifest.
- If the assistant forgets facts from the same Talk session, verify the active
  profile is not setting `voice.llm.history_max_turns = 0`, then restart
  `voice run` so the updated manifest is loaded.
