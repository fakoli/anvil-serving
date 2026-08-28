# Anvil Serving MCP bridge

This package builds the bundled, model-free stdio bridge used by
`anvil-serving mcp serve --controller-url ...`.

The bridge uses the official Model Context Protocol TypeScript SDK `2.0.0`.
Its stdio side accepts initialize-based clients through protocol
`2025-11-25` and stateless clients pinned to `2026-07-28`. Its HTTP client side
is pinned to `2026-07-28` and connects only to the authenticated Anvil
gateway/controller endpoint. Tool discovery is mirrored dynamically, including
the caller-scoped `media_*` family, so legacy Hermes-era and modern clients see
the same normalized schemas and results authorized for the bridge token. The
bridge never contains a ComfyUI endpoint, workflow graph, or controller token.

## Requirements

- Node.js 20 or newer
- npm

End users do not install this package. The generated
`anvil_serving/_node/mcp_proxy.mjs` asset ships inside the Python wheel.

## Rebuild and test

From this directory:

```bash
npm ci
npm test
npm audit
```

`npm test` type-checks and rebuilds the packaged asset, then runs the same
bridge against the exact legacy SDK generation observed in OpenClaw and the
modern SDK. The test controller also verifies bearer authentication, modern
downstream protocol metadata, schema validation before dispatch, and tool
calls, including named media workflow parity.

Commit changes to the TypeScript source, lockfile, and generated asset
together. Do not put controller token values in npm configuration, source
files, arguments, or fixtures. An OpenClaw `mcp.servers` entry may contain the
literal `${ANVIL_CONTROLLER_TOKEN}` reference; OpenClaw resolves it from the
owner-only gateway service environment.
