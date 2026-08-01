# Fakoli Dark Operator Topology

For deployment, daily commands, safety gates, and current validation coverage,
see [Fakoli Mini to Dark remote control](REMOTE-CONTROL.md).

`operator-topology.toml` is an offline-only reference for the split OpenClaw
deployment. Its addresses and GPU UUIDs are synthetic documentation values,
not a live inventory or deployment file.

The reference keeps Fakoli Mini model-free. Mini owns the OpenClaw gateway and
the persistent Realtime proxy plus local `mini-dark-audio-proxy` ports
`127.0.0.1:30110` and
`127.0.0.1:30111`; those proxies forward to Dark. It declares no Mini GPU roles
and no Mini LLM, STT, or TTS workloads.

Fakoli Dark owns the router, candidate LLM serves, and STT/TTS operations.
The topology uses a model-capable capacity policy there, while Mini has an
explicit model-free policy. The router and controller addresses are synthetic
private-network examples, and controller authentication is named by the
`ANVIL_CONTROLLER_TOKEN` environment variable only.

`anvil-router.live.toml` is a captured router recipe, not proof of a currently
qualified deployment. Its additive `[router.model_routes]` capability aliases map
`llm.primary` to `primary-local`. `llm.voice`, `vision.general`, and
`vision.ocr` map to `omni-local`. Those capability names do not classify the
physical GPUs; split-mode placement uses the equal UUID-backed
`dark-compute-a` and `dark-compute-b` roles. Embeddings, reranking, and normalized STT/TTS
remain their existing deterministic purpose/audio surfaces; on-demand ComfyUI is a
lifecycle capability, not a chat route. The recipe does not alter serve reservations
or establish a benchmark, readiness, or promotion result.

Compute B currently has two explicit single-GPU Omni choices. `omni-stack`
selects the 30B routed tier. `omni-voice-stack` selects the unpromoted Qwen2.5-Omni-3B
candidate together with the Parakeet STT and Kokoro TTS services; the shared
reservation ledger prevents both choices from being admitted together.

Both installed cards are RTX PRO 6000 Blackwell Max-Q devices with equal
declared capacity. Split mode can place compatible workloads on either role.
An optional private manifest may add one qualified TP=2 entry with
`operating_mode = "dual-gpu-exclusive"`; `serves mode` then drains all other
GPU inference before granting it both roles. The checked-in reference does not
select or promote that first TP=2 model.

Install a private deployment copy with real addresses at
`~/.anvil-serving/operator-topology.toml`. The canonical voice lifecycle is
topology-owned. Set that copy's `command_host` and `command_runtime` to the
host/runtime where each controller actually runs; the controller refuses to
self-assert a remote owner's identity.

```powershell
anvil-serving voice audio status --topology ~/.anvil-serving/operator-topology.toml --config examples/voice/openclaw-anvil-voice.toml --profile dark-audio
anvil-serving voice proxy up --topology ~/.anvil-serving/operator-topology.toml --config examples/voice/openclaw-anvil-voice.toml --profile mini-dark-audio-proxy --confirm
anvil-serving voice proxy status --topology ~/.anvil-serving/operator-topology.toml --config examples/voice/openclaw-anvil-voice.toml --profile mini-dark-audio-proxy
```

Run `voice proxy bridge` in a dedicated Mini process when the loopback proxy
ports are needed. It derives Dark targets from this topology and rejects any
non-loopback Mini listener. Configure `ANVIL_VOICE_TOPOLOGY` on both owning
controllers when using `voice_manage` or `voice_proxy_manage` directly.

Validate the complete reference without probing hosts or reading credentials:

```powershell
python -c "from anvil_serving.topology import load_topology_result; r = load_topology_result('examples/fakoli-dark/operator-topology.toml'); print(r.ok); raise SystemExit(not r.ok)"
```

`operator-topology.overlay.example.toml` is intentionally partial. Pass a private
deployment copy with `--topology-overlay`; tables merge by stable `id` before
offline validation. Do not use it as a standalone topology document and do not add
tokens, passwords, or runtime observations to either file. Real GPU UUID bindings
belong only in the gitignored private overlay.
