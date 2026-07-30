# ADR-0031 — Dual-era TypeScript MCP bridge on Fakoli Mini

- **Status:** Accepted
- **Date:** 2026-07-29
- **Supersedes:** ADR-0030's Mini-side MCP 2026-only decision
- **Relates to:** ADR-0014; ADR-0019; ADR-0030

## Context

Fakoli Dark's controller is already a constrained, authenticated MCP
`2026-07-28` service. Fakoli Mini's deployed OpenClaw
`2026.7.1-2` uses `@modelcontextprotocol/sdk` `1.29.0`, opens stdio servers
with the initialize-based `2025-11-25` era, and cannot directly consume the
modern controller contract.

The official TypeScript MCP SDK `2.0.0` serves both eras through one
`serveStdio(factory)` entry and pins a downstream client to an exact modern
protocol revision. The operator explicitly approved a Node.js dependency for
this narrow integration even though the Python package otherwise remains
stdlib-only.

## Considered options

1. Wait for OpenClaw to upgrade its bundled MCP client.
2. Add legacy lifecycle support to the network-facing Dark controller.
3. Hand-author a legacy-to-modern translation layer in Python.
4. Use the official TypeScript SDK as a dual-era Mini-side stdio bridge while
   keeping Dark modern-only.

## Decision

Use option 4.

`mcp_bridge/` pins the official client and server SDK packages to `2.0.0` and
bundles the reviewed result into the Python wheel. Remote
`anvil-serving mcp serve --controller-url ... --auth-env ...` validates the
private controller URL and credential reference in Python, then replaces
itself with the packaged Node.js bridge on POSIX hosts. Node.js 20+ is required
only for this remote MCP mode.

The bridge accepts initialize-based protocol revisions through `2025-11-25`
and stateless `2026-07-28` on stdio. It opens a bearer-authenticated downstream
client pinned to `2026-07-28`, verifies that the server identity is
`anvil-serving` at the exact installed version, fetches the restricted remote
catalog, converts its JSON Schemas with the SDK's supported adapter, and
registers only those tools. Tool arguments are independently validated before
dispatch. Token values remain in the owner-only service environment and never
enter argv, configuration, or error output. OpenClaw's declaration contains
only a `${ANVIL_CONTROLLER_TOKEN}` reference because its stdio transport
filters ambient child environments.

The Dark controller retains its existing modern-only `/mcp` implementation,
operation allowlist, idempotency store, confirmation gates, loopback bind, and
Tailscale boundary.

## Consequences

- OpenClaw's current initialize-based client and modern MCP clients operate
  the same restricted Dark tool catalog.
- There is no legacy HTTP listener and no session state in the privileged
  controller.
- Fakoli Mini must have Node.js 20+, which OpenClaw already requires.
- The wheel grows by the bundled bridge. Source, exact dependency locks,
  integrity hashes, third-party notices, type checking, and reproducible build
  commands remain in the repository.
- The Python router, controller, CLI, and local MCP implementation gain no
  third-party Python dependency.
- Tests cover the exact legacy SDK generation observed in OpenClaw, the modern
  SDK, schema rejection before dispatch, missing-token failure, token
  redaction, and the live Mini-to-Dark OpenClaw probe.
