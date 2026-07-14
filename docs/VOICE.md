# Voice Pipeline

`anvil-serving voice` is the local voice-agent runtime built in this repo. It
runs an OpenAI Realtime-compatible WebSocket server and wires a voice cascade:

```text
Realtime client
  -> anvil-serving voice proxy run
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

The voice pipeline is different from `voice sidecar`. `voice sidecar` renders
commands or compose manifests for Hugging Face's `speech-to-speech` project,
where that project owns the Realtime server. `anvil-serving voice` owns the
Realtime server and cascade itself.

## Command Summary

| Command | What It Does | What It Does Not Do |
|---|---|---|
| `anvil-serving voice audio up` | Validates the voice manifest and starts manifest-owned managed/native STT/TTS lifecycle. | Does not start the Realtime WebSocket server or the LLM router. |
| `anvil-serving voice audio down` | Stops manifest-owned managed/native STT/TTS lifecycle. | Does not stop the LLM router; a foreground `voice proxy run` process stops with Ctrl+C. |
| `anvil-serving voice audio status` | Reports bounded readiness and lifecycle state for both topology-owned audio serves. | Does not mutate either serve. |
| `anvil-serving voice audio logs` | Reads a bounded tail from both topology-owned audio serves. | Does not follow logs indefinitely. |
| `anvil-serving voice proxy run` | Starts the Realtime WebSocket server in the foreground after probing the LLM, STT, and TTS endpoints. | Does not silently continue when required endpoints are unreachable. |
| `anvil-serving voice proxy up` | Starts the Mini-owned Realtime proxy as a persistent background process. | Does not start STT/TTS models or the audio bridge. |
| `anvil-serving voice proxy down` | Stops only the persistent Realtime proxy process recorded by Anvil. | Does not stop audio models or unrelated processes. |
| `anvil-serving voice proxy restart` | Performs a bounded stop/start of the persistent Realtime proxy. | Does not replace a foreground `proxy run` process. |
| `anvil-serving voice proxy status` | Reports persistent process identity and readiness. | Does not infer health from a PID alone. |
| `anvil-serving voice proxy logs` | Reads a bounded tail of persistent proxy logs. | Does not stream without a bound. |
| `anvil-serving voice benchmark` | Runs one configured end-to-end voice turn and prints latency/quality metrics as JSON. | Does not promote routing policy or prove subjective audio quality by itself. |
| `anvil-serving voice profiles list` | Lists manifest profiles or validates the resolved manifest for one profile. | Does not mutate lifecycle or start the Realtime server. |
| `anvil-serving voice proxy bridge` | On Mini, forwards loopback STT/TTS proxy ports to topology-owned Dark endpoints. | Does not bind publicly, manage models, add auth, or inspect audio traffic. |
| `anvil-serving voice sidecar` | Validates or renders a Hugging Face speech-to-speech sidecar. | Does not run anvil-serving's native Realtime cascade. |

### Removed module-level paths

The old module-level paths (`voice up`, `down`, `start`, `stop`, `run`, and
`bridge`) are removed tombstones. They exit with replacement guidance and do
not import or invoke an operational handler.

Manifest-backed commands take `--config <voice.toml>`. If omitted,
`~/.anvil-serving/voice.toml` is used when present; otherwise the shipped
example manifest is used. `up`, `down`, `run`, and `benchmark` also accept
`--profile <name>` to apply `[voice.profiles.<name>]` before validation.
Relative managed `manifest_path` values inside the voice manifest resolve
against the voice manifest's own directory, so a host-level
`~/.anvil-serving/voice.toml` can refer to `manifest_path = "serves.toml"`.
`proxy run` and `benchmark` also accept `--candidate-overlay <toml>` and
`--candidate <label>` so live A/B tests can compose one audio topology with one
LLM candidate without copying manifests.
`benchmark` additionally accepts `--candidate-base-url`,
`--candidate-model`, and `--candidate-api-key-env` for a Fast candidate that is
already loaded on a direct OpenAI-compatible endpoint. Those flags create an
in-memory LLM overlay for that benchmark run only; they do not write the voice
manifest, router config, or production routing policy.

Audio and proxy operations require `--topology <operator-topology.toml>`. The
topology, not the machine where the command was typed, establishes the resource
owner, execution host/runtime, transport, endpoint meaning, and Mini capacity
policy. Controller-side `voice_manage` and `voice_proxy_manage` calls may use
`ANVIL_VOICE_TOPOLOGY` instead of a `topology` argument. Missing, split, or
ambiguous ownership fails before any serve or process object is constructed.

## Why These Commands Exist

Voice has four separate operational concerns:

1. **Audio model lifecycle:** STT and TTS endpoints may be Docker containers,
   native processes on a voice host, or manually managed services.
   `voice audio up/down` owns only this layer.
2. **Realtime session serving:** `voice proxy run` owns a foreground WebSocket
   server; `voice proxy up/down/restart/status/logs` own the persistent Mini
   service. Neither surface owns model lifecycle.
3. **Evidence capture:** `voice benchmark` and the hardware validation scripts
   measure whether the configured STT -> LLM -> TTS path is usable on the
   target host.
4. **Private audio forwarding:** `voice proxy bridge` listens only on Mini
   loopback and forwards to topology-derived Dark STT/TTS addresses.

Keeping those concerns separate avoids a common failure mode: a command that
appears to start "voice" but only starts one part of the pipeline. `voice audio up`
makes STT/TTS available; `voice proxy run` is the user-facing Realtime server.

## Manifest Shape

A voice manifest has one `[voice]` section and three endpoint subsections:

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
protocol = "openai"

[voice.profiles.dark-audio.stt]
base_url = "http://100.87.34.66:30110/v1"
model = "tdt-0.6b-v3"
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
- `voice.llm.speech_chunk_max_chars` caps each speakable LLM chunk before it
  is sent to TTS. Sentence punctuation still wins, but long first sentences
  are split on word boundaries so first audio does not wait for a large clause;
  the default is `72`.
- `voice.tts.protocol` defaults to `openai`, which calls
  `{base_url}/audio/speech` and consumes raw signed 16-bit PCM. Set it to
  `gepard` for Gepard's Cartesia-wire streaming TTS, where Anvil Voice connects
  to `{base_url}/tts/websocket` and consumes `chunk` messages carrying base64
  PCM. Keep `response_format = "pcm"` because the pipeline still emits raw PCM
  internally. The older `cartesia` spelling is accepted as a local wire-protocol
  alias; it is not a full Cartesia cloud integration.
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

`voice.stt.lifecycle` and `voice.tts.lifecycle` choose what `voice audio up/down`
can manage.

| Lifecycle | Use When | `voice audio up` | `voice audio down` |
|---|---|---|---|
| `managed` | The audio serve is declared in a serves manifest named by `manifest_path` (use a dedicated `serves.voice.toml`, not the shared `serves.toml` — see below). | Delegates to the same serve adapter used by `anvil-serving serves`. | Stops the matching serve. |
| `native` | The audio serve is a same-host process, such as MLX Audio on a Mac Mini or laptop. | Starts `start_command` without a shell, writes `pid_file`, logs to `log_file`, and probes `/models`. | Stops the PID it started; if no PID is present but the endpoint is up, uses optional `stop_command`. |
| `external` | Another supervisor or operator owns the process. | Skips lifecycle and reports that it was skipped. | Skips lifecycle and reports that it was skipped. |

Native lifecycle commands are trusted operator manifest content, similar to a
`serves.toml` `up` command. They are parsed with `shlex` and executed as argv
without a shell.

### Keep audio serves out of the shared model-serves manifest

Declare `managed` STT/TTS serves in a **separate manifest** (for example
`serves.voice.toml`, referenced from the voice manifest via `manifest_path`),
not in the host's main `serves.toml`. Generic hygiene over the main manifest —
`anvil-serving serves down`, the `serves_manage` MCP tool — walks every entry
and `docker stop`s it. On a host where the manifest's LLM containers are not
the currently-running ones that sweep is an invisible no-op, so nothing warns
you that adding always-on audio serves to the same file puts them in its blast
radius (observed on fakoli-dark, 2026-07-13: two clean sequential stops of the
audio serves during routine lifecycle work).

For the same reason, give the audio compose file its own explicit Compose
project (`name: anvil-voice-audio`). Compose derives the default project from
the directory, so audio services defined in a second file in the same directory
share the main project and become `--remove-orphans` bait for any invocation
against the other file. See `examples/fakoli-dark/serves.voice.toml` and
`examples/fakoli-dark/docker-compose.voice-audio.yml` for the reference shape.

The bridge-port publishes follow the directory's loopback-only default: set
`VOICE_AUDIO_PUBLISH` to the host's tailnet address (see `.env.example`) so
Mini's realtime proxy can reach `:30110`/`:30111`; without it the audio serves
bind loopback only. Audio serve entries declare `engine = "audio"` — the
truthful non-LLM label in `serves status` — rather than omitting `engine`,
which would fall back to a legacy marker guess.

## Multi-Device Expansion

Fakoli Mini and Fakoli Dark are reference devices, not fixed product roles. The
same voice topology can expand to other laptops or hosts when the configured
endpoints are reachable over Tailscale or another private or direct network path.
See [Device topologies](DEVICE-TOPOLOGIES.md) for the broader role model.

Common layouts:

- Voice and audio on one laptop: declare all resources on that host in a
  non-reference topology, then run `voice audio up` and `voice proxy run` there;
  keep STT/TTS `base_url` values on `127.0.0.1`; use `native` or `managed`
  lifecycle there.
- Voice on one laptop, LLM router on another host: keep STT/TTS local to the
  voice laptop and point `[voice.llm].base_url` at the router's private
  tailnet or direct address.
- Voice host separate from audio host: set STT/TTS `base_url` values to the
  remote private addresses and use `lifecycle = "external"` unless `voice audio up`
  is being run on the audio host itself through local CLI or a controller.
- Mini forwarding to loopback-only STT/TTS on Dark: run `voice proxy bridge`
  on Mini. It binds Mini-local `127.0.0.1` proxy ports and derives the Dark
  private target address and model ports from topology. Point the Mini voice
  profile at those loopback proxy ports.

`lifecycle = "native"` is intentionally same-host. It starts the manifest
command on the host where `anvil-serving voice audio up` runs; it is not a remote
shell transport. For remote lifecycle, run the command on the resource-owning
host or use an anvil-serving controller on that host.

Any service bound beyond loopback needs the appropriate token env var and
private network controls. Tailscale reachability is the transport requirement;
it is not a replacement for router, Realtime, or controller auth.

`voice proxy bridge` is loopback-only. Non-loopback and wildcard listeners are
rejected; there is no acknowledgement flag that weakens this invariant.

## Fakoli Mini

The checked-in Mini-local topology is an optional same-host audio example. It
keeps STT/TTS on the 16 GB Mac Mini while the LLM turn routes to the Fakoli
Dark router:

- STT: `http://127.0.0.1:30010/v1`
- TTS: `http://127.0.0.1:30011/v1`
- LLM: Fakoli Dark router over the tailnet

This is **not** the reference OpenClaw Talk or candidate benchmark topology.
Fakoli Mini's 16 GB RAM is reserved for OpenClaw Gateway, Anvil Voice
Realtime/proxy, Claude Code, and Codex. Do not run STT, TTS, or LLM model
serves on Mini during normal validation. Use this manifest only when explicitly
testing the optional Mini-local audio mode.

The checked-in manifest is `examples/voice/fakoli-mini.toml`. It uses
`lifecycle = "native"` for both audio endpoints and starts MLX Audio with PID
and log files under `/tmp/anvil-voice-mini`.

This manifest is a live reference, not a portable template. For another laptop
or router host, copy it and replace the LLM `base_url`, expected endpoint host,
expected route/model fields, MLX Audio `workdir`, and lifecycle fields for that
device.

The production reference topology intentionally refuses this manifest because
Mini is model-free. To use the optional lab mode, supply a separate topology
that declares Mini as the STT/TTS owner and explicitly permits model workloads;
do not overlay or weaken the checked-in production topology.

## OpenClaw Anvil Voice Provider

OpenClaw can use Anvil Voice as a speech-to-speech realtime provider. In that
topology the OpenClaw Gateway owns the browser or call audio relay, while
Anvil Voice owns STT, the fast-tier LLM turn, and TTS:

```text
OpenClaw Talk or Voice Call
  -> OpenClaw Gateway realtime provider "anvil"
  -> ws://127.0.0.1:8765/v1/realtime
  -> anvil-serving voice proxy run
  -> STT -> [voice.llm] anvil router -> TTS
```

Use `examples/voice/openclaw-anvil-voice.toml` for the Mini gateway layout.
It keeps the Realtime server on the Mini loopback, routes the LLM turn to the
Fakoli Dark router over the private address, and selects STT/TTS from Dark-host
audio or a Mini-side proxy to Dark for normal validation. Mini-local STT/TTS is
declared only as an explicit optional profile. It also declares profiles for
repeatable switching:

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

- `dark-audio`: Dark-host STT/TTS reached through private bridge ports
  `30110` and `30111`.
- `gepard-fast-tts`: Dark-host STT plus the experimental Gepard Fast TTS
  candidate on Dark port `39111`. Gepard is Cartesia-compatible, so the TTS
  profile uses `protocol = "gepard"` and a base URL without `/v1`.
- `mini-dark-audio-proxy`: Mini-local proxy ports `30110` and `30111` that
  forward to Dark-host STT/TTS. Use this only after that Mini-side proxy is
  actually listening.
- `mini-audio`: optional Mini-local MLX Audio STT/TTS, with conversational LLM
  prompt. Do not use it for normal OpenClaw Talk validation or LLM candidate
  A/B on the 16 GB Mini.
- `mini-validation`: Mini-local audio plus the intentional
  `I understand.` validation prompt.
- `candidate-qwen3-32b`, `candidate-gemma4-12b`, and
  `candidate-gemma4-e4b`: LLM-only A/B profiles for the checked-in Dark
  experiment serves. They preserve the base Dark-host audio path and point the
  LLM stage at direct candidate ports `39000` through `39002`. For live Talk
  A/B runs, prefer the reusable overlays in `examples/voice/candidates/` so
  the audio topology and LLM candidate remain independent.

The `mini-audio` profile lowers `voice.llm.speech_chunk_max_chars` to `56` for
the Mini-local Kokoro TTS path. In live Talk measurements this reduced first
audio latency versus the `72` character cross-topology default, while a more
aggressive `48` character split produced Kokoro stream errors on some sentence
fragments.

### Reference Mini/Dark operation

The checked-in reference topology is
`examples/fakoli-dark/operator-topology.toml`. Install a private deployment
copy with real addresses as `~/.anvil-serving/operator-topology.toml`; the
checked-in documentation addresses are intentionally not live-routable. It
declares Dark as the sole
STT/TTS model owner and Mini as the Realtime proxy plus loopback forwarding
owner. The canonical end-to-end flow is:

Each host's deployed copy must also declare that controller's real
`command_host` and `command_runtime` (or set `ANVIL_COMMAND_HOST` and
`ANVIL_COMMAND_RUNTIME`). The Dark controller must identify as the Dark audio
runtime; the Mini controller must identify as Mini native. A controller cannot
derive or self-assert the resource owner's identity.

```bash
TOPOLOGY=~/.anvil-serving/operator-topology.toml
VOICE_CONFIG=examples/voice/openclaw-anvil-voice.toml

# Preview, then start or validate Dark-owned audio through its controller.
anvil-serving voice audio up --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile dark-audio --dry-run
anvil-serving voice audio up --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile dark-audio --confirm
anvil-serving voice audio status --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile dark-audio

# In a dedicated Mini terminal, forward Mini loopback ports to Dark.
anvil-serving voice proxy bridge --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile mini-dark-audio-proxy

# Start and inspect the persistent Mini Realtime proxy.
anvil-serving voice proxy up --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile mini-dark-audio-proxy --confirm
anvil-serving voice proxy status --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile mini-dark-audio-proxy
anvil-serving voice proxy logs --topology "$TOPOLOGY" --config "$VOICE_CONFIG" --profile mini-dark-audio-proxy --tail 200
```

Use `voice proxy run` instead of `proxy up` when a foreground process is
desired. `proxy down` and `proxy restart` require `--confirm`. Audio and proxy
lifecycle are intentionally independent; neither command starts the other.

Start the voice side first. `dark-audio` has external lifecycle, so `voice audio up`
will validate the manifest and report that audio is externally managed:

```bash
anvil-serving voice profiles list --config examples/voice/openclaw-anvil-voice.toml
anvil-serving voice audio up --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio --dry-run
anvil-serving voice audio up --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio --confirm
anvil-serving voice proxy run --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio
```

To try Gepard as the Fast TTS path, start it on Fakoli Dark through the
managed serves surface. The service requires `HF_TOKEN` for first-run model
access. Keep that token in the shell, in `~/.env`, or in a gitignored
`examples/fakoli-dark/.env` copied from `examples/fakoli-dark/.env.example`;
never commit it. `anvil-serving serves` fills missing command environment
variables from `~/.env`, then `~/.anvil-serving/.env`, then the
manifest-adjacent `.env`; shell environment variables still win.
Gepard also requires a Postgres voice store, and the Dark experiment compose
starts an internal `gepard-postgres` container with the required `voices`
table initialized. Set `GEPARD_DATABASE_URL` only when you want to use an
external Postgres instead of the managed local store. The checked-in Gepard
defaults (`TTS_GPU_MEMORY_UTILIZATION=0.12`, `TTS_MAX_NUM_SEQS=4`) are a
co-residency profile for trying TTS beside the Fast LLM; raise them via env
vars only when the 5090 has enough free VRAM.

On Fakoli Dark, leave `VOICE_TTS_CANDIDATE_PUBLISH` unset for Dark-local
benchmark loops:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml up tts-gepard-fast
anvil-serving voice benchmark \
  --topology "$TOPOLOGY" \
  --config examples/voice/fakoli-dark.toml \
  --profile gepard-fast-tts \
  --evidence-out .anvil/evidence/voice-gepard-fast-tts.json
```

If the live Dark files are installed under `~/.anvil-serving`, the same flow is
shorter and checkout-independent:

```bash
anvil-serving serves up tts-gepard-fast
```

That `fakoli-dark.toml` profile marks Gepard as `managed` and names the
`tts-gepard-fast` serve. If Mini must reach this candidate directly, start the
Dark service with `VOICE_TTS_CANDIDATE_PUBLISH=100.87.34.66`.

From the Mini gateway, use the OpenClaw profile only after the Dark service is
up and reachable on Dark's private address:

```bash
anvil-serving voice proxy run --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile gepard-fast-tts
anvil-serving voice benchmark \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile gepard-fast-tts \
  --evidence-out .anvil/evidence/voice-gepard-fast-tts.json
```

The OpenClaw/Mini profile marks Gepard as `external` because Mini must not host
or manage the model process.

For a candidate LLM A/B, start the matching opt-in serve through the managed
serves surface. Leave `VOICE_CANDIDATE_PUBLISH` unset for same-host benchmark
runs; set it to the Dark host's private/tailnet address only when Mini must
reach the direct candidate endpoint:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml up voice-qwen3-32b
anvil-serving voice proxy run \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-overlay examples/voice/candidates/qwen3-32b-nvfp4.toml \
  --candidate qwen3-32b-nvfp4
anvil-serving voice benchmark \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-overlay examples/voice/candidates/qwen3-32b-nvfp4.toml \
  --candidate qwen3-32b-nvfp4 \
  --evidence-out voice-evidence/qwen3-32b-dark-audio.json
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

On Mini, start the loopback-only forwarding bridge. Listener and target ports,
the Dark host address, and ownership all come from topology; command-line
target overrides are only for explicit diagnostics:

```bash
anvil-serving voice proxy bridge \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile mini-dark-audio-proxy
```

Dark's target ports must be reachable from Mini only through the intended
private network/ACL. The bridge itself never exposes Mini beyond loopback.
Then run the Mini Realtime server with the proxy profile:

```bash
anvil-serving voice proxy run --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile mini-dark-audio-proxy
```

For a persistent service instead of a foreground terminal:

```bash
anvil-serving voice proxy up --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile mini-dark-audio-proxy --confirm
```

When testing a candidate LLM against Dark audio, compose the same candidate
overlay with either `dark-audio` or `mini-dark-audio-proxy`.

Then render or apply the matching OpenClaw config. The `--voice` flag adds the
Talk realtime block next to the normal anvil model provider config:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --voice \
  --voice-realtime-url ws://127.0.0.1:8765/v1/realtime \
  --voice-consult-model anvil/chat-fast \
  --voice-consult-thinking-level off \
  --voice-consult-bootstrap-context-mode lightweight \
  --out ./openclaw.anvil.json
```

The generated Talk config selects the OpenClaw provider id `anvil` and points
it at the Anvil Voice Realtime server. It also pins forced OpenClaw agent
consults to the low-latency `anvil/chat-fast` preset and disables consult
thinking for lower spoken-turn latency. It also keeps forced consults on
OpenClaw's lightweight bootstrap path so workspace bootstrap files such as
`MEMORY.md` are not injected into every spoken turn, without changing the
session's normal selected model:

```json5
{
  talk: {
    consultModel: "anvil/chat-fast",
    consultThinkingLevel: "off",
    consultBootstrapContextMode: "lightweight",
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
the standard chat preset. `--voice-consult-thinking-level` defaults to `off`
so old Talk configs that carried `consultThinkingLevel: "low"` are reset during
sync; raise it only when an operator deliberately trades latency for reasoning.
`--voice-consult-bootstrap-context-mode` defaults to `lightweight` and replaces
stale `talk.consultBootstrapContextMode` values during sync; set it to `full`
only when the voice workflow needs the normal OpenClaw agent bootstrap context.

Same-host Anvil Voice can omit a realtime token. If the Realtime server binds
to a private/tailnet address, set `voice.realtime_token_env` in the voice
manifest and pass `--voice-api-key-env ANVIL_VOICE_REALTIME_TOKEN` to the
harness sync command. The emitted OpenClaw config references the env var by
name; it does not contain the token value.

## Realtime Server

`voice proxy run` validates the manifest, probes the configured LLM/STT/TTS
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

Use `voice benchmark` for a quick configured end-to-end sample. For reference
OpenClaw Talk and candidate A/B, keep Mini model-free and use `dark-audio` or
`mini-dark-audio-proxy`. Run `mini-audio` only when explicitly validating the
optional same-host/local-audio mode; running it from a non-gateway checkout
only tests that checkout's loopback and is a topology negative control.

```bash
anvil-serving voice benchmark --topology "$TOPOLOGY" --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio
```

For candidate LLM A/B, keep audio topology in `--profile` and compose the LLM
candidate with an overlay:

```bash
anvil-serving voice benchmark \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-overlay examples/voice/candidates/qwen3-32b-nvfp4.toml \
  --candidate qwen3-32b-nvfp4 \
  --evidence-out .anvil/evidence/voice-qwen3-32b-dark-audio.json
```

For a candidate that is already loaded, use the direct candidate flags instead
of writing a temporary overlay. The candidate URL is relative to the host where
the benchmark command runs: use `http://127.0.0.1:<port>/v1` only on the model
host itself, and use the Dark private address when running the benchmark from
Fakoli Mini or another gateway host:

```bash
anvil-serving voice benchmark \
  --topology "$TOPOLOGY" \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-base-url http://100.87.34.66:39000/v1 \
  --candidate-model qwen3-32b-nvfp4 \
  --candidate qwen3-32b-nvfp4-dark-direct \
  --evidence-out .anvil/evidence/voice-qwen3-32b-dark-direct.json
```

Do not combine `--candidate-overlay` with the direct candidate flags. Both
paths preserve the selected audio profile and only replace `[voice.llm]` for
the benchmark process. They do not promote the candidate, change the router's
Fast preset, or make OpenClaw use the candidate outside this explicit run.

For Fast-tier LLM bakeoffs, pair `voice benchmark` with
`anvil-serving eval benchmark quality` against the same loaded endpoint and record
the final `anvil-serving serves --manifest examples/fakoli-dark/serves.toml
status` after restoring production Fast. Voice benchmark JSON is stage-latency
evidence unless the STT hypothesis and WER prove semantic transcription quality
for the prompt; do not treat first-audio timing alone as a model-promotion
gate.

Use `--profile dark-audio` only after Dark-host bridge ports are listening.
Use `--profile mini-dark-audio-proxy` only after Mini-local proxy ports
`127.0.0.1:30110` and `127.0.0.1:30111` are listening on Mini and forwarding
to Dark audio.

The JSON output includes first-audio latency, total turn latency, STT/LLM/TTS
stage durations, STT WER, TTS RTF, output byte counts, and the observed
STT/LLM text. The durable evidence envelope is
`voice-benchmark-evidence/v1` and records:

- `identity.profile`, `identity.candidate`, `identity.llm`,
  `identity.stt`, `identity.tts`, and `identity.route`.
- `topology.profile`, `topology.mode`, `topology.endpoints`, and
  `topology.mini_model_free_assertion`.
- `runs[0].latency.ttfa_ms`, `turn_latency_ms`,
  `total_turn_latency_ms`, `stt_ms`, `llm_ms`,
  `llm_stage_latency_ms`, and `tts_ms`.
- `runs[0].transcript.stt_hypothesis`, `llm_reply`, and
  `reference_text`.
- `runs[0].tool.status`, `successful`, `tool_call_count`, and `calls`.

`total_turn_latency_ms` is the end-to-end STT -> LLM -> TTS duration for the
sample turn. `llm_stage_latency_ms` is the separately timed LLM stage, so model
latency can be compared without subtracting STT or TTS time. `tool.status` is
`observed` when the candidate emitted a realtime tool call such as
`openclaw_agent_consult`; a textual claim that a tool was used is not counted
as a tool call. This is a smoke measurement, not a promotion gate.

For reference OpenClaw Talk evidence, `topology.mini_model_free_assertion` must
show a reference profile such as `dark-audio` or `mini-dark-audio-proxy`,
`mini_hosts_models = false`, and `passed = true`. Fakoli Mini must remain
model-free in this path: it runs OpenClaw Gateway, Anvil Voice
Realtime/proxy, Claude Code, and Codex, while Fakoli Dark owns the router,
candidate LLM serves, and STT/TTS endpoints or bridge ports. Use `mini-audio`
only for explicit optional same-host Mini-local audio validation.

Interpret stage timing before swapping models:

- Treat a stage as dominant when its p50 elapsed time is at least half of total
  turn latency, or at least twice the next-largest stage across comparable
  successful runs.
- Work on the LLM/model path when LLM p50 is the dominant stage, or when LLM
  first-output is above about `300 ms` while STT and TTS first-output are below
  their thresholds.
- Work on STT when STT p50 exceeds about `200 ms`, WER is unacceptable, or STT
  errors are present.
- Work on TTS/chunking when TTS p50 exceeds about `350 ms`, TTS first-output
  exceeds about `250 ms`, or the TTS stream errors on normal spoken chunks.
- If no stage dominates, prefer cheaper prompt/chunk/profile tuning before
  loading a new model.

The current T005/T006 evidence does **not** justify promoting a candidate LLM.
The only successful timing row was gathered on the now-optional Mini-local
audio path (`ttfa_ms 611.29`, `turn_latency_ms 789.06`, `stt_ms 106.28`,
`llm_ms 356.82`, `tts_ms 325.95`), where LLM and TTS were co-dominant rather
than a clear model-only bottleneck. Candidate rows were retained as topology
negative controls because they failed before STT from a wrong-host loopback
path. Gather comparable successful data with Dark-host or Mini-proxied audio
before any production promotion, and keep promotion behind the normal human
`router_promote` gate.

For live Realtime Talk sessions, `voice proxy run` also emits redacted
`voice_stage_timing` log lines for the core `stt`, `llm`, and `tts` stages.
Use those lines to attribute latency without exposing prompts or transcripts:

```text
voice_stage_timing stage=llm input_type=GenerateRequest turn_id=rt-turn-7 generation=12 text_chars=84 elapsed_ms=912.4 first_output_ms=488.1 output_count=2 error=false
```

`elapsed_ms` is the full stage duration for that input. `first_output_ms`
shows when the first downstream item was available, which is the useful value
for perceived first-audio latency in streaming LLM and TTS stages. Text values
are logged as character counts only.

If `tts first_output_ms` is high for a large `text_chars` value, lower
`voice.llm.speech_chunk_max_chars` in the active voice profile before changing
models. That keeps the same answer path but starts TTS on smaller word-boundary
chunks.
For the optional Mini-local Kokoro path, keep the checked-in `mini-audio`
override near `56` unless fresh `voice_stage_timing` evidence shows a better
value; values near `48` have produced stream errors in live A/B tests.
If Kokoro closes a TTS stream before producing any audio for a chunk, the TTS
stage retries once and can fall back to a separator-safe spoken form such as
`up to date` instead of `up-to-date`; failures after audio has started still
surface as real stage errors.

For explicit optional Mini-local audio proof, use the hardware validation
harness:

```bash
python scripts/voice/mini_validation.py --report
```

That report adds target-host checks, router auth proof, endpoint model identity
proof, post-benchmark STT/TTS memory attribution, and a verdict. A non-Mini run
is a negative control unless explicitly allowed with `--allow-unsupported`.
This harness is not the reference OpenClaw Talk topology because Mini should
remain model-free for normal validation.

## MCP And Controller Operation

Agents and OpenClaw should prefer `voice_manage` for STT/TTS lifecycle:

```json
{
  "action": "up",
  "config": "examples/voice/openclaw-anvil-voice.toml",
  "profile": "dark-audio",
  "topology": "~/.anvil-serving/operator-topology.toml"
}
```

The first call previews the plan and returns a dry-run command. A live mutation
requires:

```json
{
  "action": "up",
  "config": "examples/voice/openclaw-anvil-voice.toml",
  "profile": "dark-audio",
  "topology": "~/.anvil-serving/operator-topology.toml",
  "confirm": true,
  "dry_run": false
}
```

This mirrors `serves_manage` and `router_manage`: exact target first, then an
explicit confirmed call. `status` and `logs` are immediate bounded reads and do
not require confirmation. The Dark controller may set `ANVIL_VOICE_TOPOLOGY`
instead of receiving `topology` on every call.

Use `voice_proxy_manage` for Mini's persistent Realtime process. Its actions are
`up`, `down`, `restart`, `status`, and `logs`; mutations require the same
preview/confirm sequence. Set `ANVIL_VOICE_TOPOLOGY` on the Mini controller or
pass `topology` explicitly. Neither MCP tool starts the other subsystem.

## Troubleshooting

- `voice audio up` says lifecycle is external: change the manifest to `managed` or
  `native`, or keep managing that endpoint manually.
- `voice proxy run` refuses to start: check the `/models` endpoint for the LLM, STT,
  and TTS base URLs named in the manifest.
- Native `voice audio down` reports `ready_but_unmanaged`: the endpoint is answering,
  but no PID file or `stop_command` can identify what to stop.
- Optional Mini-local STT/TTS logs live under `/tmp/anvil-voice-mini` in the
  checked-in Mini-local manifest.
- Router auth errors should be fixed by setting the env var named in
  `voice.llm.api_key_env`; do not paste token values into the manifest.
- If the assistant forgets facts from the same Talk session, verify the active
  profile is not setting `voice.llm.history_max_turns = 0`, then restart
  `voice proxy run` so the updated manifest is loaded.
