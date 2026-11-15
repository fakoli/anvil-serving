# Third-party notices

The bundled Mini-side MCP adapter includes:

- `@modelcontextprotocol/client` 2.3.0
- `@modelcontextprotocol/server` 2.3.0
- `@modelcontextprotocol/core` 2.3.0
- their production dependencies recorded in `package-lock.json`

The SDK's package metadata declares Apache-2.0. Its LICENSE describes the
Apache-2.0 transition and retains MIT terms for contributions without relicensing
consent. Exact dependency identities and integrity hashes are in
`package-lock.json`. The build derives the included packages from esbuild's
input inventory and ships their complete license/notice texts beside the bundle
as `anvil_serving/_node/THIRD_PARTY_NOTICES.txt`, including in the Python wheel.
