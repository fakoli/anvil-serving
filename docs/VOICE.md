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
| `anvil-serving voice up` | Validates the voice manifest and starts managed STT/TTS serves. | Does not start the Realtime WebSocket server or the LLM router. |
| `anvil-serving voice start` | Alias for `voice up`. | Same as `up`. |
| `anvil-serving voice down` | Stops managed STT/TTS serves. | Does not stop the LLM router; a foreground `voice run` process stops with Ctrl+C. |
| `anvil-serving voice stop` | Alias for `voice down`. | Same as `down`. |
| `anvil-serving voice run` | Starts the Realtime WebSocket server in the foreground after probing the LLM, STT, and TTS endpoints. | Does not silently continue when required endpoints are unreachable. |
| `anvil-serving voice benchmark` | Runs one configured end-to-end voice turn and prints latency/quality metrics as JSON. | Does not promote routing policy or prove subjective audio quality by itself. |

All commands take `--config <voice.toml>`. If omitted, the shipped example
manifest is used when present.

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

[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "parakeet"
lifecycle = "managed"

[voice.tts]
base_url = "http://127.0.0.1:30011/v1"
model = "kokoro"
lifecycle = "managed"
response_format = "pcm"
```

Manifest hygiene follows the rest of the repo:

- Use `127.0.0.1` for same-host URLs.
- Use private tailnet or direct addresses for cross-device endpoint URLs.
- Store credentials in environment variables and reference only the env-var
  name with `*_env` keys.
- Do not embed credentials in URLs.
- A non-loopback `realtime_host` requires `realtime_token_env`.

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

Fakoli Mini and fakoli-dark are reference devices, not fixed product roles. The
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

`lifecycle = "native"` is intentionally same-host. It starts the manifest
command on the host where `anvil-serving voice up` runs; it is not a remote
shell transport. For remote lifecycle, run the command on the resource-owning
host or use an anvil-serving controller on that host.

Any service bound beyond loopback needs the appropriate token env var and
private network controls. Tailscale reachability is the transport requirement;
it is not a replacement for router, Realtime, or controller auth.

## Fakoli Mini

The checked-in Mini topology is one concrete example: audio stays local on the
16 GB Mac Mini while the LLM turn routes to fakoli-dark:

- STT: `http://127.0.0.1:30010/v1`
- TTS: `http://127.0.0.1:30011/v1`
- LLM: fakoli-dark router over the tailnet

The checked-in manifest is `examples/voice/fakoli-mini.toml`. It uses
`lifecycle = "native"` for both audio endpoints and starts MLX Audio with PID
and log files under `/tmp/anvil-voice-mini`.

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
  "config": "examples/voice/fakoli-mini.toml"
}
```

The first call previews the plan and returns a dry-run command. A live mutation
requires:

```json
{
  "action": "up",
  "config": "examples/voice/fakoli-mini.toml",
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
