# OpenClaw live-validation runbook

> **✅ DONE (2026-06-30), Gaps 1-3.** All three original gaps were run live against OpenClaw
> **2026.6.6** on the gateway. Results + the two bugs the live run caught (wire form needed
> `providerOverride` + bare model; the manifest needed `configSchema`) are in the companion notes
> repo `fakoli/anvil-serving-notes` (`findings/2026-06-30-openclaw-live-validation.md`).
> The steps below remain as the reproducible procedure.
>
> **⚠️ NEW DEFECT FOUND (2026-07-01), Gap 4.** A real agent turn caught a fourth issue: the
> keyless-503 → native-failover safety net is **not reliable** once `before_model_resolve` has
> emitted `providerOverride:"anvil"`. See Gap 4 below — this is a live-confirmed, unresolved gap with no
> known repo-side fix (OpenClaw-side behavior).
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
The plugin emits `providerOverride: "anvil"` plus a bare `modelOverride: "<preset>"` for local
presets. Confirm what actually goes out on the wire.
1. Point the `anvil` provider's `baseUrl` at the router (or a capture proxy).
2. Send one user message; capture the outbound request body.
3. Inspect the `model` field. Record whether OpenClaw forwards bare `planning` or a namespaced
   variant such as `anvil/planning`.
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
`package.json` pins `compat.pluginApi: ">=2026.4.21"` — the floor currently used by the checked-in
plugin for `before_model_resolve`.
1. On the gateway, check the installed OpenClaw version and its CHANGELOG/release tags for the
   `before_model_resolve` introduction.
2. **Acceptance:** keep `compat.pluginApi` aligned across `package.json`, this runbook, and the
   integration spec; if a gateway release proves the hook requires a higher floor, raise all three
   together.

## Gap 4 — anvil-503 native-failover loop (LIVE-CONFIRMED DEFECT, 2026-07-01)
A real OpenClaw agent turn hit this live: with the T008 plugin routing a local-preferred class to
anvil (`providerOverride:"anvil"`) and anvil returning 503 (local unable), OpenClaw's native failover
(`agents.defaults.model.fallbacks -> [openai/gpt-5.5, openai/gpt-5.4-mini]`) fired but **both**
fallback attempts also resolved through the `anvil` provider and 503'd again — the turn never reached
the native cloud provider.
1. Reproduce: install the plugin, configure `agents.defaults.model.fallbacks` with a real native
   model, force anvil to 503 for a local-preferred class (e.g. point the `anvil` provider's `baseUrl`
   at a router instance with no local tiers bound, or an unreachable local backend), send a
   quick-edit/review/chat/long-context turn.
2. Capture the outbound requests for the primary attempt AND every fallback attempt (proxy log or
   `openclaw` debug logging) — confirm which provider/model each one actually resolves to on the
   wire.
3. **Acceptance:** every fallback attempt's outbound request should target the native provider named
   in `agents.defaults.model.fallbacks`. **Observed (2026-07-01): it does not** — see
   `docs/OPENCLAW-INTEGRATION-SPEC.md` §7 for the full root-cause writeup and
   `docs/adr/0005-anvil-503-native-failover-unreliable.md` for the ADR record. No repo-side fix is
   available; operator mitigations are documented in
   `plugins/openclaw-anvil-intent-router/README.md`.

## Record the outcome
Append the captured request, the fire-cadence log, and the confirmed `pluginApi` floor to a dated
finding in the companion notes repo (`fakoli/anvil-serving-notes`), and update `plugins/openclaw-anvil-intent-router/README.md` + its
`package.json` to the confirmed values. Once all three pass live, the OpenClaw beachhead is
field-proven, not just source-verified.
