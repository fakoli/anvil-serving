---
title: "anvil-serving x OpenClaw direct-alias contract"
date: 2026-07-26
status: current-contract
---

# OpenClaw integration contract

OpenClaw uses anvil-serving as an OpenAI-compatible provider. It sends a configured
direct alias to the router, which proxies that alias to its one declared local tier.
There is no intent plugin, request classifier, decision endpoint, quality-profile lookup,
or automatic cross-model fallback.

Use `anvil-serving harness sync openclaw` to render or apply provider configuration.
The generated provider uses the router base URL, an environment-variable reference for its
token, and the configured model alias. Do not hand-edit Anvil-owned provider keys when the
sync command can render them.

When merging into an existing OpenClaw document, sync refreshes only the
`anvil/*` allowlist entries, preserves unrelated provider models, and retains
an existing structured file/env SecretRef for the Anvil provider. It never
replaces a structured SecretRef with a plaintext token.

## Contract

- The gateway calls `POST /v1/chat/completions` or `POST /v1/messages` with an explicit alias.
- The router accepts only aliases declared in `[router.model_routes]`; unknown aliases receive
  a clean 404.
- A configured alias is proxied only to its declared local tier. A non-ready or quiesced tier
  receives a 503; the router does not select an alternate model.
- `GET /v1/models` advertises the configured aliases and purpose-model endpoints.
- Router tokens are environment variables, never literal provider configuration values.
- Lifecycle, preflight, benchmark, and promotion are explicit Anvil Serving operations. They are
  outside the request path.

## Setup and validation

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.64.0.10:8000/v1 \
  --confirm
```

After a serving or alias configuration change, render a preview, apply to the intended gateway,
restart that gateway if requested, then validate the selected local model directly:

```bash
anvil-serving eval preflight \
  --base-url http://127.0.0.1:<serve-port>/v1 \
  --model <served-model> \
  --confirm
```

Use `GET /v1/decisions` for metadata-only diagnostics such as requested alias, selected tier,
status, and token counts. It is not a routing-policy or model-selection API.

For voice, OpenClaw Gateway remains the client-facing realtime service. The Anvil Voice sidecar
uses the same explicit LLM alias after STT and before TTS; see [Voice pipeline](VOICE.md).
