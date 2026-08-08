# `voice audio *` silently ignores `--topology-overlay`

**Status:** Open (verified in code 2026-08-07)

## Problem

`anvil_serving/voice/cli.py::_resolve_audio_operation` loads the topology
without the overlay:

```python
topology = load_topology(topology_path)
```

while its sibling `_resolve_proxy_operation` does it correctly:

```python
topology = load_topology(topology_path, args.topology_overlay)
```

The overlay path is still *recorded* in the result context (`overlay=` label),
so `voice audio up|down|status|logs --topology-overlay <file>` looks like it
honored the overlay while resolving targets against the base document only.

## Impact

- An operator using a deployment overlay (the documented
  `operator-topology.overlay.example.toml` pattern) gets base-topology target
  resolution for audio lifecycle commands — silently. If the overlay re-homes
  or re-addresses the stt/tts serves, `voice audio` acts on the wrong
  declaration with no error.
- The envelope's `overlay` context field makes the output actively misleading.

## Expected

`voice audio *` resolves against the merged base+overlay document, identically
to `voice proxy *`.

## Notes

Found while relocating STT/TTS to a new voice host (2026-08-07). The
`forward_resolution_options=True` double-resolution path is what makes this
reachable: the dispatcher resolves the merged document, then the voice leaf
re-resolves — without the overlay.
