# OpenClaw Anvil Voice Extension

This guide is the Anvil Serving source of truth for the OpenClaw speech-to-speech
extension. It covers the operator contract: what Anvil Serving owns, what
OpenClaw owns, how to render the matching OpenClaw config, and how to validate
the full path before treating the provider as ready.

## Outcome

OpenClaw can offer an `anvil` realtime voice provider next to its other voice
providers. OpenClaw still owns the browser, desktop, or call audio relay. Anvil
Serving owns the speech cascade:

```text
OpenClaw Talk or Voice Call
  -> OpenClaw Gateway provider "anvil"
  -> anvil-serving Realtime WebSocket
  -> STT
  -> anvil router fast-tier LLM turn
  -> TTS
  -> OpenClaw audio playback
```

The point of the extension is to reuse Anvil Serving's measured local-first
routing policy for voice turns without making OpenClaw manage STT, TTS, model
serves, benchmark evidence, or router promotion decisions.

## Ownership Boundary

| Layer | Owner | Responsibility |
|---|---|---|
| Voice lifecycle | Anvil Serving | Start or validate STT/TTS endpoints with `anvil-serving voice up`; stop manifest-owned endpoints with `voice down`. |
| Realtime server | Anvil Serving | Run the OpenAI Realtime-compatible WebSocket server with `anvil-serving voice run`. |
| LLM turn | Anvil Serving router | Route `[voice.llm]` requests to the configured fast tier, verified tier, exhaustion path, or opt-in cloud tier. |
| Harness config rendering | Anvil Serving | Render the OpenClaw model provider and Talk realtime block with `anvil-serving harness sync openclaw --voice`. |
| Gateway relay | OpenClaw | Accept microphone or call audio, relay frames to the configured provider id, and play the returned audio. |
| Provider selection | OpenClaw | Show and select provider id `anvil` in the same operator surface as other realtime voice providers. |

Keep this boundary intact. Normal OpenClaw operation should not hand-edit Anvil
Serving manifests, and normal Anvil Serving operation should not bypass
OpenClaw's gateway/provider selection.

## Required Pieces

- An Anvil Serving router reachable from the voice host through
  `voice.llm.base_url`.
- STT and TTS endpoints configured in a voice manifest.
- A foreground `anvil-serving voice run` process exposing
  `ws://<voice-host>:<port>/v1/realtime`.
- OpenClaw with the `anvil-voice` provider extension installed and selected.
- A rendered OpenClaw config from `anvil-serving harness sync openclaw --voice`.

The checked-in reference manifest is
`examples/voice/openclaw-anvil-voice.toml`. Treat it as a working example, not
as a portable secret-bearing config.

## Quick Path

Preview and start the speech endpoints:

```bash
anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml --dry-run
anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml
```

Run the Realtime server in the foreground:

```bash
anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml
```

Render the matching OpenClaw provider config:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://127.0.0.1:8000/v1 \
  --voice \
  --voice-realtime-url ws://127.0.0.1:8765/v1/realtime \
  --out ./openclaw.anvil.json
```

On split-host deployments, replace `127.0.0.1` with the router or voice host's
private address only where the other device must connect to it. Keep same-host
endpoint URLs on `127.0.0.1`.

In OpenClaw, select provider id `anvil` for Talk or Voice Call. The gateway
should relay audio to the Realtime URL rendered by Anvil Serving.

## Token And Cost Rules

- Same-host Realtime development can omit an Anvil Voice token.
- A non-loopback Realtime bind must set `voice.realtime_token_env` in the voice
  manifest and pass the same env-var name through `--voice-api-key-env`.
- Router auth belongs in `[voice.llm].api_key_env`; never paste token values
  into a manifest, OpenClaw config, log, or PR.
- OpenClaw config rendered by Anvil Serving stores secret references by env-var
  name only.
- Voice turns use the router policy already configured for `[voice.llm].model`.
  Metered cloud remains off unless the router config explicitly enables a cloud
  tier and lists the work class in `[router].metered_cloud`.
- `voice benchmark` and validation scripts are evidence gathering only. They do
  not promote a profile or change routing policy.

## Validation Checklist

Run these checks before calling the OpenClaw provider ready:

| Check | Command or evidence | Pass signal |
|---|---|---|
| Manifest preview | `anvil-serving voice up --config examples/voice/openclaw-anvil-voice.toml --dry-run` | The plan names the expected STT/TTS lifecycle actions and no secrets are printed. |
| Endpoint readiness | `anvil-serving voice run --config examples/voice/openclaw-anvil-voice.toml` | Startup probes the LLM, STT, and TTS endpoints and binds `/v1/realtime`. |
| End-to-end voice turn | `anvil-serving voice benchmark --config examples/voice/openclaw-anvil-voice.toml` | JSON includes STT text, LLM text, TTS bytes, first-audio latency, and total latency. |
| Hardware proof when using the Mini reference | `python scripts/voice/mini_validation.py --report` | Report includes router auth proof, endpoint model identity, memory attribution, and a verdict. |
| OpenClaw config render | `anvil-serving harness sync openclaw --config configs/example.toml --voice --voice-realtime-url ws://127.0.0.1:8765/v1/realtime --out -` | Output includes provider id `anvil` and no literal secret values. |
| Gateway smoke | OpenClaw Talk or Voice Call using provider `anvil` | Gateway opens a Realtime session and receives audio output from Anvil Serving. |

For implementation evidence and prior adversarial review notes, see
`docs/findings/2026-07-openclaw-anvil-voice-option.md`.

## Adversarial Review Prompts

Use independent reviewers for these checks before merging behavior changes:

- Does OpenClaw remain only a relay/provider selector, or did the change leak
  STT/TTS/router ownership into the gateway?
- Can a non-loopback Realtime URL be configured without a token? That should
  fail at the Anvil Serving manifest layer.
- Does any generated config contain literal token material instead of env-var
  names?
- Does the provider silently route to metered cloud, or is cloud still governed
  by the router's explicit opt-in policy?
- Does the validation evidence prove audio ingress, STT, LLM routing, TTS, and
  audio egress separately?
- Are same-host URLs written as `127.0.0.1`?

## Troubleshooting

- **Provider is missing in OpenClaw:** confirm the `anvil-voice` extension is
  installed and restart the OpenClaw gateway after applying rendered config.
- **Realtime connect fails:** confirm `voice run` is still foreground and that
  OpenClaw is using the rendered `realtimeUrl`.
- **401 from Realtime:** ensure the env var named in `voice.realtime_token_env`
  is set on the Anvil Serving host and the same env-var name is rendered into
  OpenClaw with `--voice-api-key-env`.
- **Router auth fails:** set the env var named by `[voice.llm].api_key_env` on
  the voice host.
- **Speech works but response quality is weak:** benchmark STT, router, and TTS
  separately before changing policy. Poor STT or TTS quality should not be fixed
  by promoting a router profile.
- **Split-host latency is high:** keep STT/TTS near the audio device, keep the
  router on the measured model host, and use private network addresses only for
  the cross-device legs that require them.

## Related Docs

- [Voice pipeline](VOICE.md) for the full `anvil-serving voice` command surface.
- [OpenClaw integration spec](OPENCLAW-INTEGRATION-SPEC.md) for the broader
  OpenClaw adapter contract.
- [Operator playbooks](OPERATOR-PLAYBOOKS.md) for MCP/controller operation.
- [Device topologies](DEVICE-TOPOLOGIES.md) for split-host role placement.
