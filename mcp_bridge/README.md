# Anvil Serving MCP bridge

This package builds the bundled, model-free stdio bridge used by
`anvil-serving mcp serve --controller-url ...`.

The bridge uses the official Model Context Protocol TypeScript SDK `2.0.0`.
Its stdio side accepts initialize-based clients through protocol
`2025-11-25` and stateless clients pinned to `2026-07-28`. Its HTTP client side
is pinned to `2026-07-28` and connects only to the authenticated Anvil
controller.

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
calls.

Commit changes to the TypeScript source, lockfile, and generated asset
together. Do not put controller tokens in npm configuration, source files,
arguments, or fixtures.
