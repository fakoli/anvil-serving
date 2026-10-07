# Voice

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Full voice guide](../VOICE.md)

Use the `voice` family to operate the reference speech topology without
confusing proxy ports with model ownership. The declared topology owner runs
the STT and TTS model serves. Companion Node may own the realtime proxy and
loopback forwarders, but it is model-free by default.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Operate one co-located managed stack | `voice up --dry-run` | Apply with `--confirm`; `voice down` stops it in reverse dependency order. |
| Inspect audio readiness | `voice audio status` | Read a bounded tail with `voice audio logs`. |
| Change STT/TTS lifecycle | `voice audio up --dry-run` | Apply with `--confirm`; `down` stops native processes and stops/removes managed containers. |
| Run the realtime proxy interactively | `voice proxy run` | Interrupt the foreground process when finished. |
| Manage the background proxy | `voice proxy status` | Preview `up`, `down`, or `restart`, then apply with `--confirm`. |
| Forward Mini-local audio ports | `voice proxy bridge --dry-run` | Run the bridge in the foreground after reviewing both routes. |
| Measure a voice candidate | `voice benchmark` | Retain structured evidence with `--evidence-out`. |
| Qualify an STT model | `voice corpus prepare` | Validate it, then run `voice benchmark --scope stt`. |
| Measure TTS generation over a text corpus | `voice benchmark --scope tts` | Retain audio outside Git, then obtain independent STT and human listening evidence. |
| Inspect configuration overlays | `voice profiles list` | Validate one resolved overlay with `voice profiles validate`. |
| Prepare an optional sidecar | `voice sidecar validate` | Render a host command or Compose skeleton; neither command launches it. |

## Command map

### Operate one co-located managed stack

| Command | Purpose |
| --- | --- |
| `voice up` | Start managed STT, TTS, then the managed realtime proxy. |
| `voice down` | Stop the managed realtime proxy, then managed STT/TTS. |

### Operate topology-owned audio serves

| Command | Purpose |
| --- | --- |
| `voice audio status` | Read bounded STT/TTS readiness from the declared owner. |
| `voice audio logs` | Read a bounded log tail for each owned audio serve. |
| `voice audio up` | Preview or start managed/native STT and TTS serves. |
| `voice audio down` | Preview or stop native STT/TTS processes and stop/remove managed containers. |

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
| `voice benchmark` | Replay one end-to-end session, or a reusable STT/TTS corpus, against resolved endpoints. |
| `voice corpus prepare` | Build the deterministic 24-human/6-synthetic English STT corpus. |
| `voice corpus validate` | Fail closed on malformed JSONL, audio metadata, paths, or hashes. |
| `voice profiles list` | List named overlays without contacting a service. |
| `voice profiles validate` | Validate one fully merged profile offline. |

### Prepare the optional sidecar

| Command | Purpose |
| --- | --- |
| `voice sidecar validate` | Validate sidecar URLs, models, image, and secret references. |
| `voice sidecar command` | Render shell-safe host argv without executing it. |
| `voice sidecar compose` | Render a loopback-bound Compose service without writing or running it. |

## Co-located stack lifecycle

`voice up` and `voice down` are deliberately narrow aggregates for one managed
host. The STT, TTS, and realtime-proxy tables must all use `lifecycle =
"managed"`, reference the same serves manifest, and belong to its `voice`
group. STT/TTS use host-relative `127.0.0.1` URLs for host callers, while the
managed proxy joins the audio-owned `anvil-voice` Docker network and uses the
`stt` / `tts` service names. The command refuses native, external, incomplete,
or split-host configurations before lifecycle I/O.

Preview the complete dependency order before applying:

```bash
anvil-serving voice up --config ~/.anvil-serving/voice.toml --dry-run
anvil-serving voice up --config ~/.anvil-serving/voice.toml --confirm
anvil-serving voice down --config ~/.anvil-serving/voice.toml --dry-run
anvil-serving voice down --config ~/.anvil-serving/voice.toml --confirm
```

Bring-up is STT/TTS first, then the proxy. A failed audio bring-up skips the
proxy. Tear-down is the reverse; a failed proxy stop leaves audio running so a
still-live proxy does not lose its dependencies. Both commands emit one
combined result covering the audio and proxy phases.

The aggregate is manifest-owned and does not require topology resolution. If
topology options are supplied explicitly, the command verifies that STT, TTS,
and the realtime proxy resolve to one Docker owner before importing the
handler.

The reference Mini/Dark topology is intentionally split and must keep using
the explicit `voice audio ...` and `voice proxy ...` commands below. The
aggregate does not compose separate controller operations across owners.

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

For a speech baseline, pass a local regular PCM16, mono, 16-kHz RIFF WAV and
its verbatim transcript together. The file must be no longer than 30 seconds;
a symbolic link in the input-file path's final component, non-regular files,
compressed or malformed WAVs, stereo audio, other sample rates, and any
header/data size mismatch are rejected before the benchmark contacts an
endpoint:

```bash
anvil-serving voice benchmark --scope end-to-end \
  --input-wav artifacts/voice/utterance.wav \
  --reference-text "the recorded utterance, exactly as spoken" \
  --evidence-out artifacts/voice/speech-baseline.json
```

The evidence retains the input kind, source-WAV and PCM byte counts and
SHA-256 values, audio format, duration, and transcript SHA-256. A valid WAV
only establishes container properties: the input is labelled
`supplied-content-unverified`, so retain corpus provenance separately before
interpreting it as human speech. Supplying a WAV still measures the current
serialized STT → LLM → TTS replay. Its structured `measurement_scope` records
`serialized-stage-replay`, `first-nonempty-yielded-TTS-chunk`,
`realtime=false`, and `acoustic_playback=false`. `ttfa_ms` is `null` when TTS
yields no audio, and otherwise records the first nonempty chunk yielded after
any stage-side buffering; it is not a first-response-byte or audible-playback
measurement.
Without these two options, the command retains compatibility by using a
220-Hz synthetic tone. Its input identity says `synthetic-tone-not-speech` and
`not-qualifying`; it is only a wire-path smoke, never speech-baseline evidence.

The benchmark records resolved model and endpoint identity with its end-to-end
STT, router, and TTS metrics. Evidence output is restricted to the workspace or
configured evidence root. Evidence publication is atomic and refuses to
overwrite an existing file, including when another writer publishes the same
path concurrently. Select a new `--evidence-out` filename for every attempt.
Unreachable dependencies return nonzero and do not
create a successful measurement record.

### STT corpus

Prepare the corpus into an evidence workspace:

```bash
anvil-serving voice corpus prepare --config ~/.anvil-serving/voice.audio-dark.toml --out artifacts/stt/corpus
anvil-serving voice corpus validate --manifest artifacts/stt/corpus/manifest.jsonl --expected-cases 30
```

The versioned JSONL manifest resolves audio paths against itself and accepts
only 16-kHz mono WAV/FLAC with matching SHA-256. Run a candidate through an
STT-only overlay:

```bash
anvil-serving voice benchmark --scope stt \
  --config ~/.anvil-serving/voice.audio-dark.toml \
  --corpus artifacts/stt/corpus/manifest.jsonl \
  --repetitions 3 --concurrency 1 \
  --stt-candidate-overlay examples/primary-node/stt-experiments/overlays/nemotron35-asr.toml \
  --evidence-out artifacts/stt/nemotron-sequential.json
```

Use a separate `--repetitions 1 --concurrency 4` lane.
`--auto-language-probes 6` records six additional unconditioned human
requests; it does not qualify multilingual behavior. Evidence uses
`stt-benchmark-evidence/v1`, writes atomically, and exits nonzero with
`complete=false` if an expected request fails.

Prepare a separate 120-human English holdout corpus with 60 utterances from
each LibriSpeech split, excluding the canonical corpus:

```bash
anvil-serving voice corpus prepare --config ~/.anvil-serving/voice.toml \
  --out artifacts/stt/holdout --human-cases-per-split 60 --synthetic-cases 0 \
  --exclude-manifest artifacts/stt/corpus/manifest.jsonl
anvil-serving voice corpus validate --manifest artifacts/stt/holdout/manifest.jsonl --expected-cases 120
```

`--human-cases-per-split` accepts multiples of three from 3 through 300 and
balances short, medium, and long source durations equally in each split.
`--synthetic-cases` accepts 0–6 fixed phrases; zero sends no TTS requests.
The default remains 12 human cases per split plus six synthetic cases.
Exclusion validates the supplied manifest, filters exact case IDs and source
identities before deterministic selection, and rejects normalized-audio hash
overlap. Supplement preparation validates its exact requested count and
rejects duplicate audio hashes or IDs before publishing the output directory.
`provenance.json` retains the exclusion manifest hash and identities, actual
duration-bucket counts, archive checksums, source FLAC hashes, and normalized
WAV hashes; the command also returns its provenance hash. Failed preparation
leaves no partial published corpus.

### TTS corpus

Measure an already-running TTS endpoint using the selected voice manifest and
profile. Store audio in a new directory outside every Git worktree; retain
JSON under the workspace or a configured evidence root:

```bash
anvil-serving voice benchmark --scope tts \
  --config ~/.anvil-serving/voice.toml --profile dark-audio \
  --corpus artifacts/tts/prompts.jsonl --expected-cases 24 \
  --repetitions 3 --concurrency 1 \
  --audio-out ~/.local/share/anvil-serving/tts-audio/run-001 \
  --evidence-out artifacts/tts/run-001.json
```

Each contiguous JSONL line has exactly these fields:

```json
{"schema_version":"tts-corpus/v1","id":"short-01","text":"Please cancel the reminder.","category":"short-correction","language":"en"}
```

The command rejects duplicate IDs or JSON fields, blank text, unsupported
schemas, unsafe ID path tokens, extra fields, count mismatches, and oversized
corpora before contacting the endpoint. It accepts 1–128 cases, 1–20 warm
repetitions, and at most 1024 warm requests. TTS concurrency is currently 1.
The optional `--expected-cases` supplies an exact count gate. The output
directory must be new and contain no symbolic-link ancestors or parent
traversal.

Use `--tts-candidate-overlay candidate.toml` to apply a TTS-only overlay after
the profile. A candidate overlay contains only `[voice.tts]` and the optional
`[tts_benchmark.identity]` table. Identity accepts `served_name`, `checkpoint`,
`revision`, `runtime`, `runtime_version`, `image`, `image_digest`, `hardware`,
and `quantization`, as nonempty strings. `--candidate` supplies a label;
otherwise the overlay filename supplies it. These declarations describe the
operator's pinned candidate and are not an endpoint attestation.

The supported endpoint contract is OpenAI HTTP `/v1/audio/speech`. The
manifest's `voice_id`, when declared, is sent as `voice`; otherwise evidence
labels voice selection `server-default-unverified`. Declared `language` is
also sent without inferring language from each corpus case. Authentication
uses the configured `api_key_env` reference and rejects an unset variable.
The request preserves the exact model, voice, language, and response format,
and refuses HTTP redirects. LLM/STT candidate options are rejected for this
scope.

`voice.tts.stream` is an explicit Boolean request option and defaults to
`true`. Set it to `false` for a declared buffered adapter; the same setting
reaches both corpus benchmarks and the live voice pipeline. Evidence retains
the selected setting and labels buffered first-audio timing accordingly:
the first body data may arrive only after synthesis completes. The command
does not retry a rejected streaming request as a buffered request.

`voice.tts.max_tokens` optionally sends an explicit OpenAI HTTP generation
cap. It accepts integers from 1 through 8192; Boolean, fractional, zero,
negative, and larger values are rejected. When absent, the request omits the
field and retains the server default. A declared value applies identically
to the live pipeline and corpus benchmark and is retained in the resolved
configuration and its hash. Set it in the manifest or TTS candidate overlay;
changing the cap defines a new tested configuration and does not modify
historical comparison evidence. The server determines what one generation
token represents for its model.

The evidence schema is `tts-benchmark-evidence/v1`. It retains the exact
request endpoint and configuration, source manifest/overlay hashes, resolved
TTS configuration hash, corpus hash and file order, a separate first-observed
cold request, and every warm request in repetition then JSONL order. The cold
label does not imply empty endpoint caches: the command never resets a model
or operates its lifecycle. Failures remain in the evidence, and
`complete=true` requires every expected request and zero failures. Incomplete
runs write evidence atomically and exit nonzero.

Each request records response status, byte count and SHA-256, first body read,
first complete audio frame observed in an HTTP read, request-through-EOF
latency, source audio duration, and client read chunk timings. WAV headers are
excluded from first-audio timing. Chunk timings describe HTTP reads and may
include client buffering; they do not establish server synthesis boundaries
or acoustic playback. The end-to-end metric excludes decoding and artifact
writes. Chunk logs retain at most the first 64 reads per request and 16,384
reads across the complete run, including the cold request. Each request
reports retained and omitted counts and explicit truncation. First-frame
timing remains independent of these log limits.

`summary.warm_artifact_inclusive_wall_seconds` includes HTTP requests,
decoding, resampling, file writes, fsync, artifact hashing, and advisory
screening. It describes harness elapsed time; endpoint throughput is
explicitly unmeasured. Per-request latency and RTF continue to use HTTP
request-through-EOF timing. The two RTF orientations are explicit:

| Evidence field | Definition | Faster direction |
| --- | --- | --- |
| `generation_seconds_per_audio_second` | Request-through-EOF seconds / source audio seconds | Lower |
| `audio_seconds_per_generation_second` | Source audio seconds / request-through-EOF seconds | Higher |

The declared response format is honored. Raw `pcm` means signed 16-bit
little-endian mono at `source_sample_rate`; `wav` uses the validated container's
rate and accepts mono PCM16 or IEEE float32. WAV is permitted only in this TTS
benchmark context; realtime voice configuration still requires PCM. The
original response is retained beside a normalized mono PCM16 WAV at
`target_sample_rate`. Normalization uses whole-response linear interpolation
without an anti-aliasing filter, so listening at the native rate remains useful.
Nonfinite float samples are replaced with zero for normalization and counted.
Responses are bounded to 16 MiB and 120 audio seconds; the entire audio
artifact directory is bounded to 512 MiB. Evidence contains only relative
audio artifact names and filesystem-path hashes, and never embeds audio byte
bodies.

Blank signals, nonfinite samples, repeated 100-ms waveform blocks, and
identical audio returned for different text are deterministic advisory flags.
They neither prove intelligibility nor automatically fail a quality gate.
Independent STT fidelity validation, human listening, and listening preference
remain separate and explicitly `not_run` or `not_measured` in this evidence.
The command does not measure playback or cancellation and never promotes a
candidate.

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
