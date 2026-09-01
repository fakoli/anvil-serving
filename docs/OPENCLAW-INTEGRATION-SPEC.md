---
title: "Anvil Serving and OpenClaw capability-alias contract"
date: 2026-07-26
status: current-contract
---

# OpenClaw integration contract

OpenClaw uses Anvil Serving as an OpenAI-compatible provider. It sends a configured
capability alias to the gateway, which proxies that alias to its one declared local tier.
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

When the router exposes an image-capable `vision.general` alias, sync also
sets `agents.defaults.imageModel.primary` to `anvil/vision.general`. This lets
OpenClaw's image-understanding tool use the explicit vision route while the
ordinary agent remains on `llm.primary`. A non-Anvil image-model selection is
operator-owned and remains unchanged.

## Contract

- The gateway calls `POST /v1/chat/completions` or `POST /v1/messages` with an explicit alias.
- The router accepts only aliases declared in `[router.model_routes]`; unknown aliases receive
  a clean 404.
- A configured alias is proxied only to its declared local tier. A non-ready or quiesced tier
  receives a 503; the router does not select an alternate model.
- `GET /v1/models` advertises configured aliases with each alias's effective
  configured or inference-reported `context_window` and its router-owned
  `max_output_tokens`; clients such as Hermes can consume that standard
  discovery metadata without an explicit per-model override.
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

OpenClaw and Pi releases that do not consume the authenticated capability
endpoint can be reconciled locally from the same router contract:

```bash
anvil-serving harness sync clients \
  --base-url https://router.example.ts.net/v1 \
  --restart-openclaw-on-change \
  --dry-run
```

This sync preserves credentials and compaction policy, validates the router
status/capability-alias closure, and keeps limits for distinct aliases independent.
Apply only
after reviewing the config hash and model matrix, using `--confirm`.

When a tier uses `metadata_source = "upstream"`, an inference-service model or
context replacement at the same endpoint is visible after the router's bounded
metadata refresh interval. OpenClaw continues sending the stable alias; neither
its provider configuration nor the router route table needs a model-name edit.

Use `GET /v1/decisions` for metadata-only diagnostics such as requested alias, selected tier,
status, and token counts. It is not a routing-policy or model-selection API.

For voice, OpenClaw Gateway remains the client-facing realtime service. The Anvil Voice sidecar
uses the same explicit LLM alias after STT and before TTS; see [Voice pipeline](VOICE.md).
