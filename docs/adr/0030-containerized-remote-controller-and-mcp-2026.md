# ADR-0030 — Containerized Dark controller and MCP 2026-only remote operation

- **Status:** Accepted
- **Date:** 2026-07-29
- **Relates to:** ADR-0014; ADR-0019; ADR-0028

## Context

Fakoli Mini owns OpenClaw but is intentionally model-free. Fakoli Dark owns
Docker Desktop, both GPUs, the router, and model-serving containers. Running
the existing operator directly on Windows creates compatibility and dependency
drift, while running it on Mini would move execution away from the resources
it owns.

MCP `2026-07-28` replaced connection initialization with stateless
per-request metadata and `server/discover`. The Anvil controller has not been
released, so carrying both the old and new protocol eras would add an
unnecessary compatibility surface.

## Considered options

1. Run all Anvil Serving commands over SSH from Mini.
2. Run the controller natively on Dark Windows.
3. Run a Linux controller container on Dark with Docker-socket access and use
   SSH only for native-host break glass.
4. Retain the previous MCP initialization flow alongside MCP `2026-07-28`.

## Decision

Use option 3 and support only MCP `2026-07-28`.

The controller image includes the Anvil Serving package, a pinned Docker CLI,
and the Compose plugin. It runs non-root with a read-only root filesystem,
dropped capabilities, a durable idempotency volume, an explicit operation
allowlist, read-only declared configuration mounts, NVIDIA visibility, and the
Docker socket. Exact `127.0.0.1` endpoints are rewritten inside that container
to a declared host alias.

Docker publishes the controller only on Dark's Windows loopback. Host-owned
Tailscale Serve provides the authenticated tailnet path. OpenClaw on Mini runs
the Anvil MCP stdio proxy against that endpoint. SSH is not an automatic
fallback and remains available only for operations whose declared runtime is
native.

The JSON-RPC endpoint is `/mcp`. Each request carries the `2026-07-28`
protocol version and client capabilities in `_meta`; HTTP metadata must agree
with the body. `server/discover`, `tools/list`, and `tools/call` are supported.
Responses declare `resultType`, cacheable results declare `ttlMs` and
`cacheScope`, and server identity is returned in result metadata.
`initialize` and `initialized` are not implemented.

## Consequences

- Mini can operate Dark without hosting models or receiving the Docker socket.
- The Linux image avoids most Windows Python and shell compatibility issues.
- The container deliberately has no operator home, SSH keys, or GitHub CLI
  credentials. Git- and SSH-authenticated workflows remain outside it.
- Docker-socket membership is effectively authority over Docker despite the
  non-root UID. Token secrecy, loopback publishing, tailnet policy, the
  restricted catalog, and minimal mounts are required controls.
- Portable main, flexibility, and voice manifests work from the container.
  Unmounted experimental manifests fail explicitly instead of gaining broad
  filesystem access.
- Pre-release clients using an older MCP initialization flow must upgrade;
  there is no compatibility mode.
