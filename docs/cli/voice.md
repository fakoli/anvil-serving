# Voice

[CLI overview](../CLI.md) · [Control plane & integrations](control-plane.md) · [Full voice guide](../VOICE.md)

The `voice` family manages Dark-owned STT/TTS services, the realtime proxy and bridge,
end-to-end benchmarks, profiles, and the optional speech-to-speech sidecar. This page
is the command map; the [voice guide](../VOICE.md) contains deployment details.

## Commands

| Family | Commands | Purpose |
| --- | --- | --- |
| Audio | `voice audio up`, `down`, `status`, `logs` | Manage STT/TTS serve lifecycle. |
| Proxy | `voice proxy run`, `up`, `down`, `restart`, `status`, `logs`, `bridge` | Manage the realtime proxy and Mini-to-Dark bridge. |
| Benchmark | `voice benchmark` | Benchmark an end-to-end voice session. |
| Profiles | `voice profiles list`, `validate` | Inspect and validate voice profiles. |
| Sidecar | `voice sidecar validate`, `command`, `compose` | Validate or render a speech-to-speech sidecar. |

## Audio lifecycle

```bash
anvil-serving voice audio status --topology ~/.anvil-serving/operator-topology.toml
anvil-serving voice audio up --topology ~/.anvil-serving/operator-topology.toml --dry-run
anvil-serving voice audio logs --topology ~/.anvil-serving/operator-topology.toml
anvil-serving voice audio down --topology ~/.anvil-serving/operator-topology.toml --dry-run
```

In the reference topology, Dark owns STT and TTS model endpoints. Mini-local ports
`127.0.0.1:30110` and `127.0.0.1:30111` are proxy endpoints forwarding to Dark, not
local model serves. `mini-audio` is an explicit optional same-host mode.

## Realtime proxy

```bash
anvil-serving voice proxy status --topology ~/.anvil-serving/operator-topology.toml
anvil-serving voice proxy run --topology ~/.anvil-serving/operator-topology.toml --help
anvil-serving voice proxy bridge --topology ~/.anvil-serving/operator-topology.toml --help
anvil-serving voice proxy logs --topology ~/.anvil-serving/operator-topology.toml
```

Use `proxy run` for a foreground process and lifecycle leaves for managed operation.
Logs and status are bounded.

## Benchmark

```bash
anvil-serving voice benchmark --help
```

The benchmark covers an end-to-end voice session. Record the selected profile and
topology with the result so local endpoints are not mistaken for local model ownership.

## Profiles

```bash
anvil-serving voice profiles list
anvil-serving voice profiles validate --profile PROFILE
```

Validation checks the selected profile before starting dependent services.

## Speech-to-speech sidecar

```bash
anvil-serving voice sidecar validate --help
anvil-serving voice sidecar command --help
anvil-serving voice sidecar compose --help
```

These commands validate and render sidecar configuration; they do not bypass normal
serve ownership or quality gates.

## Related references

- [Voice deployment and operations](../VOICE.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
- [Troubleshooting](../TROUBLESHOOTING.md)
