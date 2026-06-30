# OpenClaw live-validation runbook

> **✅ DONE (2026-06-30).** All three gaps were run live against OpenClaw **2026.6.6** on the
> gateway. Results + the two bugs the live run caught (wire form needed `providerOverride` + bare
> model; the manifest needed `configSchema`) are in
> [`findings/2026-06-30-openclaw-live-validation.md`](findings/2026-06-30-openclaw-live-validation.md).
> The steps below remain as the reproducible procedure.
>
> **Original status:** the tooling, plugins, and fixtures (T013 + T014) are merged and pass against
> **synthetic** fixtures. This runbook is the **live** step — run by an operator on the gateway box
> where OpenClaw is installed. It closes the three validate-first gaps from
> [`OPENCLAW-INTEGRATION-SPEC.md`](OPENCLAW-INTEGRATION-SPEC.md) §0/§3.

## Why these are "validate-first"
The OpenClaw plugin (`plugins/openclaw-anvil-intent-router/`) was built against the *source-read*
hook contract, but three facts can only be confirmed against a running gateway. Until they are, the
plugin is correct-by-construction but not field-proven.

## Prerequisites
- The gateway box with OpenClaw installed and reachable; the anvil-serving router running and
  reachable from the gateway (`anvil-serving serve --config <cfg>`), and the router's serves up
  (`docker ps` → `:30000` heavy, `:30001` fast). Use `127.0.0.1`, never `localhost` (Windows IPv6
  stall).
- The reference plugin installed per `plugins/openclaw-anvil-intent-router/README.md` (including the
  **required** `plugins.entries.openclaw-anvil-intent-router.hooks.allowConversationAccess=true` gate
  and the **required** `anvil` provider block with `models[]` for every preset id).

## Gap 1 — wire value (bare `<preset>` vs `anvil/<preset>`)
The plugin emits `modelOverride: "anvil/<preset>"`. Confirm what actually goes out on the wire.
1. Point the `anvil` provider's `baseUrl` at the router (or a capture proxy).
2. Send one user message; capture the outbound request body.
3. Inspect the `model` field. Record whether it is `anvil/planning` or bare `planning`.
4. **Acceptance:** the router accepts **both** (verified: `validate.py` `WIRE_FORM_RE` +
   `intent.parse_model` strips the `anvil/` prefix). So either is fine — but record which OpenClaw
   sends, and pin the README to it.
   ```
   python examples/openclaw/validate.py --assert-wire-form --capture captured-request.json
   ```

## Gap 2 — fire cadence (per user message, not per session)
The whole "classify each turn" premise needs `before_model_resolve` to fire **once per user
message**, above the attempt loop.
1. Install the **logging** hook (`examples/openclaw/logging-hook/`, T013) alongside (or instead of)
   the router plugin — it records every fire to `hook-fire-log.jsonl` and routes nothing.
2. Run a **multi-turn** conversation (≥3 user messages, including at least one retried run).
3. Validate the produced log:
   ```
   python examples/openclaw/validate.py --assert-fire-cadence real-hook-fire-log.jsonl
   ```
4. **Acceptance:** exactly one fire per user message (`userMessageIndex` strictly increases, one per
   message; a repeated `runId` for the same message → cadence violation, flagged non-zero).

## Gap 3 — `pluginApi` compat floor
`package.json` pins `compat.pluginApi: ">=2026.4.21"` — the spec's **UNCONFIRMED** estimate for when
`before_model_resolve` landed.
1. On the gateway, check the installed OpenClaw version and its CHANGELOG/release tags for the
   `before_model_resolve` introduction.
2. **Acceptance:** pin `compat.pluginApi` to the confirmed floor; if the installed version is older,
   the hook won't fire and the plugin must declare a higher floor (or document the minimum).

## Record the outcome
Append the captured request, the fire-cadence log, and the confirmed `pluginApi` floor to a dated
finding in the companion notes repo (`fakoli/anvil-serving-notes`), and update `plugins/openclaw-anvil-intent-router/README.md` + its
`package.json` to the confirmed values. Once all three pass live, the OpenClaw beachhead is
field-proven, not just source-verified.
