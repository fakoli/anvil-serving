# Fakoli Dark Operator Topology

`operator-topology.toml` is an offline-only reference for the split OpenClaw
deployment. Its addresses and GPU UUIDs are synthetic documentation values,
not a live inventory or deployment file.

The reference keeps Fakoli Mini model-free. Mini owns the OpenClaw gateway and
the local `mini-dark-audio-proxy` ports `127.0.0.1:30110` and
`127.0.0.1:30111`; those proxies forward to Dark. It declares no Mini GPU roles
and no Mini LLM, STT, or TTS workloads.

Fakoli Dark owns the router, candidate LLM serves, and STT/TTS operations.
The topology uses a model-capable capacity policy there, while Mini has an
explicit model-free policy. The router and controller addresses are synthetic
private-network examples, and controller authentication is named by the
`ANVIL_CONTROLLER_TOKEN` environment variable only.

Validate the complete reference without probing hosts or reading credentials:

```powershell
python -c "from anvil_serving.topology import load_topology_result; r = load_topology_result('examples/fakoli-dark/operator-topology.toml'); print(r.ok); raise SystemExit(not r.ok)"
```

`operator-topology.overlay.example.toml` is intentionally partial. Pass a private
deployment copy with `--topology-overlay`; tables merge by stable `id` before
offline validation. Do not use it as a standalone topology document and do not add
tokens, passwords, or runtime observations to either file. Real GPU UUID bindings
belong only in the gitignored private overlay.
