# Voice

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Full voice guide](../VOICE.md)

Use the `voice` family to operate the reference speech topology without
confusing proxy ports with model ownership. Fakoli Dark owns STT and TTS model
serves. Fakoli Mini may own the realtime proxy and loopback forwarders, but it
is model-free by default.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Inspect audio readiness | `voice audio status` | Read a bounded tail with `voice audio logs`. |
| Change STT/TTS lifecycle | `voice audio up --dry-run` | Apply with `--confirm`; use `down` to stop owned serves. |
| Run the realtime proxy interactively | `voice proxy run` | Interrupt the foreground process when finished. |
| Manage the background proxy | `voice proxy status` | Preview `up`, `down`, or `restart`, then apply with `--confirm`. |
| Forward Mini-local audio ports | `voice proxy bridge --dry-run` | Run the bridge in the foreground after reviewing both routes. |
| Measure a voice candidate | `voice benchmark` | Retain structured evidence with `--evidence-out`. |
| Inspect configuration overlays | `voice profiles list` | Validate one resolved overlay with `voice profiles validate`. |
| Prepare an optional sidecar | `voice sidecar validate` | Render a host command or Compose skeleton; neither command launches it. |

## Command map

### Operate Dark-owned audio serves

| Command | Purpose |
| --- | --- |
| `voice audio status` | Read bounded STT/TTS readiness from the declared owner. |
| `voice audio logs` | Read a bounded log tail for each owned audio serve. |
| `voice audio up` | Preview or start managed/native STT and TTS serves. |
| `voice audio down` | Preview or stop only managed/native STT and TTS serves. |

### Operate the Mini realtime layer

| Command | Purpose |
| --- | --- |
| `voice proxy run` | Run the authenticated realtime WebSocket proxy in the foreground. |
| `voice proxy status` | Inspect owned background-proxy process state. |
| `voice proxy logs` | Read a bounded background-proxy log tail. |
| `voice proxy up` | Preview or start the owned background proxy. |
| `voice proxy down` | Preview or stop the owned background proxy. |
| `voice proxy restart` | Preview or restart the same owned proxy instance. |
| `voice proxy bridge` | Forward Mini-local STT/TTS ports to Dark in the foreground. |

### Evaluate and inspect configuration

| Command | Purpose |
| --- | --- |
| `voice benchmark` | Replay one end-to-end voice session against resolved endpoints. |
| `voice profiles list` | List named overlays without contacting a service. |
| `voice profiles validate` | Validate one fully merged profile offline. |

### Prepare the optional sidecar

| Command | Purpose |
| --- | --- |
| `voice sidecar validate` | Validate sidecar URLs, models, image, and secret references. |
| `voice sidecar command` | Render shell-safe host argv without executing it. |
| `voice sidecar compose` | Render a loopback-bound Compose service without writing or running it. |

## Audio lifecycle

Every audio lifecycle command requires topology because loopback is
host-relative and STT/TTS are model workloads. Inspect before changing state:

```bash
anvil-serving voice audio status --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio
anvil-serving voice audio logs --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio --tail 50
```

`status` uses a three-second readiness deadline per managed serve by default;
`--ready-timeout` accepts 0.1 through 60 seconds. `logs` defaults to 200 lines
per serve and accepts 1 through 5000. Native file reads are capped at 1 MiB.

Preview and apply use the same owner, runtime, and lifecycle resolution:

```bash
anvil-serving voice audio up --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio --dry-run
anvil-serving voice audio up --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio --timeout-seconds 300 --confirm
anvil-serving voice audio down --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio --dry-run
anvil-serving voice audio down --topology ~/.anvil-serving/operator-topology.toml --profile dark-audio --confirm
```

The command refuses split STT/TTS ownership, local execution against a remote
owner, and lifecycle/runtime mismatches. Managed endpoints require Docker;
native endpoints require a native runtime. External endpoints are reported and
left untouched. Mini runs model workloads only when topology explicitly marks
an experimental same-host mode such as `mini-audio`.

Audio `up` and `down` share an overall 300-second lifecycle deadline. Override
it with `--timeout-seconds` from 1 through 7200 seconds; the deadline covers
both STT and TTS subprocess work rather than resetting for each serve.

## Realtime proxy

The reference proxy profile keeps models on Dark while the realtime session
layer runs on Mini:

```bash
anvil-serving voice proxy run --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy
```

`run` resolves the Mini owner, verifies that the manifest listener matches the
topology endpoint, and probes the router, STT, and TTS endpoints before binding.
It remains in the foreground until interrupted. A candidate overlay is scoped
to that process:

```bash
anvil-serving voice proxy run --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --candidate voice-candidate --candidate-overlay candidate.toml
```

For managed background operation, inspect and preview first:

```bash
anvil-serving voice proxy status --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy
anvil-serving voice proxy logs --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --tail 50
anvil-serving voice proxy up --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --dry-run
anvil-serving voice proxy up --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --confirm
anvil-serving voice proxy restart --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --confirm
anvil-serving voice proxy down --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --confirm
```

The default owned records are
`~/.anvil-serving/run/voice-proxy.pid` and `voice-proxy.log`. Status and
lifecycle commands reject stale, reused, or foreign PID state. Logs are a
bounded tail rather than a follow stream.

The bridge is a separate foreground process. Mini-local ports
`127.0.0.1:30110` and `127.0.0.1:30111` forward to Dark; they are not local
model serves:

```bash
anvil-serving voice proxy bridge --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy --dry-run
anvil-serving voice proxy bridge --topology ~/.anvil-serving/operator-topology.toml --profile mini-dark-audio-proxy
```

Topology supplies listen ports and Dark target addresses. The listener must
remain `127.0.0.1`; `localhost`, wildcard targets, and public target IPs are
rejected.

## Benchmark

Benchmark the resolved manifest or one already-loaded candidate:

```bash
anvil-serving voice benchmark --profile mini-dark-audio-proxy --candidate current-fast
anvil-serving voice benchmark --candidate-base-url http://127.0.0.1:30001/v1 --candidate-model MODEL --evidence-out artifacts/voice/candidate.json
```

`--candidate-overlay` applies after the selected profile. A direct candidate
URL and model must be supplied together; the optional token flag names an
environment variable. These overrides never rewrite the manifest.

The benchmark records resolved model and endpoint identity with its end-to-end
STT, router, and TTS metrics. Evidence output is restricted to the workspace or
configured evidence root. Unreachable dependencies return nonzero and do not
create a successful measurement record.

## Profiles

Profiles are offline manifest overlays:

```bash
anvil-serving voice profiles list
anvil-serving voice profiles list --config ~/.anvil-serving/voice.toml
anvil-serving voice profiles validate --profile dark-audio
anvil-serving voice profiles validate --config ~/.anvil-serving/voice.toml --profile mini-dark-audio-proxy
```

`list` validates the base manifest and prints declared names. `validate`
requires one name, merges that overlay, and checks the resolved schema. Neither
verb resolves topology, probes an endpoint, starts a service, or changes the
manifest.

## Speech-to-speech sidecar

The sidecar helpers are offline renderers. Validate first:

```bash
anvil-serving voice sidecar validate --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
anvil-serving voice sidecar validate --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml --json
```

Validation rejects inline secrets, credential-bearing URLs, unsafe container
loopback, malformed service values, and endpoints that do not end in `/v1` or
`/v1/realtime` as required.

Render host argv or a Compose skeleton without launching anything:

```bash
anvil-serving voice sidecar command --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
anvil-serving voice sidecar command --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml --with-auth --json
anvil-serving voice sidecar compose --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml
anvil-serving voice sidecar compose --config examples/huggingface-speech-to-speech/openclaw-gateway.example.toml --service-name voice-sidecar --with-auth
```

`--with-auth` emits only an environment-variable reference. It does not read
or print the token. Compose output publishes the realtime port on
`127.0.0.1`, uses the container-specific router URL, and never invokes Docker
or writes a file.

## Related references

- [Voice deployment and operations](../VOICE.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
- [Troubleshooting](../TROUBLESHOOTING.md)
