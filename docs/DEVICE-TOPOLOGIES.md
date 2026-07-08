# Device Topologies

anvil-serving treats machine names as deployment facts, not product roles.
Fakoli Mini and Fakoli Dark are the current reference devices, but the same
architecture should expand to additional laptops, workstations, or small edge
hosts when they are reachable over Tailscale or another private or direct network
path.

## Roles

| Role | Owns | Current Reference Example |
|---|---|---|
| Gateway host | OpenClaw gateway, harness runtime, gateway-local restart/apply actions. | Fakoli Mini |
| Voice host | `anvil-serving voice run`, microphone/speaker path, and Realtime/proxy orchestration. | Fakoli Mini |
| Audio model host | STT/TTS model endpoints and optional private bridge ports. | Fakoli Dark or another non-Mini audio host |
| Router host | `anvil-serving serve` or deployed router container, router config, token auth, decision logs. | Fakoli Dark |
| Serve host | GPU/CPU LLM serves, `serves.toml`, model cache, preflight and benchmark target endpoints. | Fakoli Dark |
| Controller host | `anvil-serving controller serve` for structured remote operations on the resources it owns. | Usually the router/serve host |
| Operator client | Codex, Claude Code, OpenClaw, or another tool calling MCP/controller tools. | Any trusted host |

A single device can hold several roles. A laptop can be a voice host, a router
host, a gateway host, or all three. The important boundary is ownership: run
lifecycle commands on the host that owns the process, config, manifest, and
logs being changed.

In the current reference OpenClaw topology, Fakoli Mini is intentionally
model-free: its 16 GB RAM is reserved for OpenClaw Gateway, Anvil Voice
Realtime/proxy, Claude Code, and Codex. Do not place STT, TTS, or LLM model
serves on Mini for reference validation or candidate benchmarking. Mini-local
audio remains an optional same-host mode for explicit local-audio tests only.

## Connectivity Requirements

- Same-host URLs use `127.0.0.1`.
- Cross-device URLs use a private reachable address: Tailscale tailnet DNS/IP,
  private LAN/VPN address, or another direct private route.
- Public interface exposure is not the default product contract. Treat it as a
  separate security decision that needs an explicit human gate.
- Tailscale or direct reachability is necessary but not sufficient for
  management operations. Use service tokens and tailnet ACLs together.
- Credentials stay in environment variables. Config files and manifests should
  name env vars such as `ANVIL_ROUTER_TOKEN` or `ANVIL_CONTROLLER_TOKEN`, not
  literal token values.

There are two planes:

| Plane | Examples | Cross-Device Rule |
|---|---|---|
| Data plane | Router front door, STT endpoint, TTS endpoint, model serve endpoint. | The caller's configured `base_url` must be reachable and authenticated if exposed beyond loopback. |
| Control plane | `anvil-serving mcp`, `anvil-serving controller serve`, guarded lifecycle tools. | The resource-owning host runs the controller; the operator/gateway host bridges to it with `--controller-url` and `--auth-env`. |

## Expansion Patterns

### Add Another Laptop As A Voice Host

Install anvil-serving and the chosen STT/TTS stack on the laptop. If STT/TTS
run on the same laptop as `voice run`, keep those endpoint URLs on
`127.0.0.1` and use `lifecycle = "native"` or `lifecycle = "managed"` as
appropriate.

Point `[voice.llm].base_url` at the reachable router host:

```toml
[voice.llm]
base_url = "http://anvil-gpu.tailnet.example:8000/v1"
model = "chat-fast"
api_key_env = "ANVIL_ROUTER_TOKEN"
```

If STT/TTS are remote from the voice host, set their `base_url` values to the
remote private addresses and use `lifecycle = "external"` unless the lifecycle
command is being run on the audio host itself through local CLI or a controller.
`lifecycle = "native"` starts a process on the host running `voice up`; it is
not a remote shell mechanism.

If the audio host's STT/TTS services are intentionally loopback-only, expose
private bridge ports with the product utility on the audio host:

```bash
anvil-serving voice bridge \
  --listen-host 100.87.34.66 \
  --stt-listen-port 30110 \
  --tts-listen-port 30111 \
  --i-understand-this-exposes-voice-audio
```

Then point the voice host manifest profile at those private bridge ports and
keep the STT/TTS lifecycle as `external`.
Use a concrete private or tailnet address for `--listen-host`; wildcard binds
require `--allow-wildcard-listen` and should be reserved for firewall-scoped
deployments.

### Add Another Laptop As A Router Or Serve Host

Run the router or model serves on that laptop, bind them to a private reachable
address, and require token auth when the service is not loopback-only. Update
gateway, voice, or benchmark configs to use the laptop's private address.

For lifecycle and diagnostics, prefer a controller on the same laptop:

```bash
export ANVIL_CONTROLLER_TOKEN="<generate-and-store-out-of-band>"
anvil-serving controller serve \
  --host anvil-gpu.tailnet.example \
  --port 8766 \
  --auth-token-env ANVIL_CONTROLLER_TOKEN
```

Operator clients then bridge to that controller instead of shelling into the
host:

```bash
anvil-serving mcp \
  --controller-url http://anvil-gpu.tailnet.example:8766 \
  --auth-env ANVIL_CONTROLLER_TOKEN
```

### Add Another Gateway Host

A gateway host needs the router base URL reachable from that gateway and the
right auth env vars available in the gateway process. Gateway-local actions,
such as OpenClaw restart/apply, should stay local to that gateway. Remote
router, serve, and benchmark operations should go through the controller on the
resource-owning host.

For OpenClaw Talk, the gateway host may also run Anvil Voice Realtime/proxy.
That does not mean it should host STT/TTS/LLM models. Point audio profiles at
remote private endpoints or at a same-gateway proxy that forwards to the audio
model host; use same-host/native audio only when the task explicitly tests that
optional mode.

## Operator Checklist

Before adding a new device, record:

- Which role or roles the device owns.
- The private address other devices will use to reach it.
- Which services bind to loopback and which bind to a private address.
- Which env vars provide service auth.
- Which controller, if any, owns lifecycle operations on that device.
- Which validation proves the device is ready: `router_status`,
  `serves_status`, `voice_manage` preview, `preflight_probe`, or
  `voice benchmark`.
