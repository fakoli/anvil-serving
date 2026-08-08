# Voice-proxy compose: `command:` is swallowed by the router image ENTRYPOINT, and `VOICE_CONFIG_DIR` default never exists

**Status:** Open (both verified 2026-08-07; fixed locally in an operator home)

## Problem 1 — proxy service silently runs a router

`docker-compose.voice-proxy.yml` (shipped scaffold and `examples/fakoli-dark/`)
starts the realtime proxy by overriding `command:` on the release image:

```yaml
image: ${VOICE_IMAGE:-anvil-serving:0.22.0}
command: [anvil-serving, voice, proxy, run, --host, "0.0.0.0", --port, "8765", --config, /etc/anvil/voice.toml]
```

But the documented image build (`docker build -t anvil-serving:0.22.0 .`)
produces the **default/router target**, whose ENTRYPOINT is:

```dockerfile
ENTRYPOINT ["sh", "-c", "exec anvil-serving router run --config \"${ANVIL_CONFIG:-/etc/anvil/config.toml}\" --host 0.0.0.0 --port 8000"]
```

`sh -c` receives the compose `command:` items as `$0…$n` and never references
them — so the "proxy" container actually execs **a second router**. Verified
against a live deployment's `anvil-serving:0.22.0` tag (router entrypoint) on
2026-08-07; the proxy container had evidently never been exercised there.

Local fix that works: `entrypoint: []` on the service (verified — proxy came
up healthy, `/usage` 200).

## Problem 2 — `VOICE_CONFIG_DIR` default points at a directory `init` never creates

The same service mounts the voice manifest via:

```yaml
volumes:
  - "${VOICE_CONFIG_DIR:-./voice}:/etc/anvil:ro"
```

`anvil-serving init` scaffolds `voice.toml` at the operator-home **root** and
never creates a `voice/` subdirectory, so the default mount is an empty
auto-created directory and the proxy fails on a missing
`/etc/anvil/voice.toml`. The operator must hand-create `voice/voice.toml` (a
copy that can drift from the canonical file) or point `VOICE_CONFIG_DIR` at a
directory that exposes more than the manifest.

## Impact

- `voice proxy up` / `voice up` from a scaffolded home cannot start the proxy
  as shipped: first the wrong process (router), then a missing config mount.
- The wrong-process failure mode is silent from compose's perspective — the
  container starts and stays "Up" (as a router without a config).

## Expected

- Proxy service pins `entrypoint: []` (or the image gains a dedicated
  proxy target), so `command:` is honored.
- `init` creates the `voice/` staging directory (or the compose mounts the
  canonical `voice.toml` path directly).
