# Preserve the router API prefix through Tailscale Serve

Status: fixed; independent adversarial review passed
Priority: P1

The existing edge renderer targeted `/v1` at the router origin without a path,
and its tests/docs assumed Tailscale preserved the public mount. Tailscale's
[`serveWebHandler` at revision 5201273aec737d6372ab7423c31c04ca3ca2a0c2](https://github.com/tailscale/tailscale/blob/5201273aec737d6372ab7423c31c04ca3ca2a0c2/ipn/ipnlocal/serve.go#L1128)
strips the matched mount. The generated mapping therefore sent `/v1/models`
to `/models`, violating the router API contract.

Built-in and port-only `/v1` mappings now target `/v1` explicitly. Other
port-only mounts still target the service root, and full URLs preserve their
declared target paths. Old mappings appear as drift; exact target matching
prevents `down` from silently removing them. The operator must review and apply
the corrected mapping through the managed edge surface, then verify the actual
endpoint. No live mapping was inspected or changed during this source fix.

Validation: 35 edge tests passed, covering defaults, TOML and CLI overrides,
explicit paths, old-mapping drift, and removal ownership. The new remote bundle
uses the same path-retaining `/v1` target. Independent review and final source
gates are recorded in the
[delivery ticket](2026-09-05-router-request-diagnostics.md).
