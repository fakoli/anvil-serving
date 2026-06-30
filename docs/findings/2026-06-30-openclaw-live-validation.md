# OpenClaw live validation — results (2026-06-30)

> The T013/T014 "validate-first" gaps, settled against the **live OpenClaw gateway** on
> **Fakoli-Mini** (OpenClaw **2026.6.6**), with the router-side capture endpoint on **fakoli-dark**.
> Supersedes the UNCONFIRMED estimates in `docs/OPENCLAW-INTEGRATION-SPEC.md` and the open items in
> `docs/OPENCLAW-LIVE-VALIDATION.md`.

## Setup
- **Gateway:** OpenClaw `2026.6.6`, `~/.openclaw`, gateway on `:18789` (Fakoli-Mini, macOS).
- **Plugin:** `plugins/openclaw-anvil-intent-router/` installed via `openclaw plugins install --link`,
  enabled with `plugins.entries.openclaw-anvil-intent-router.hooks.allowConversationAccess: true`.
- **Provider:** an `anvil` provider (`api: openai-completions`, `mode: merge`, the 5 preset models)
  pointed at a stdlib capture endpoint on `http://fakoli-dark:8000/v1` that records the outbound
  `model` and returns a valid completion.
- **Turns driven** with `openclaw agent -m "<msg>" --session-key … --json` (one agent turn = one
  user message). Gateway restored to baseline afterward (plugin uninstalled, config reverted).

## Gap results

### ✅ 1. Wire value — **bare preset, via `providerOverride`**
The original plugin emitted `modelOverride: "anvil/<preset>"` and assumed the `anvil/` prefix named
the provider. **It does not.** OpenClaw treats `modelOverride` as a *model id*, so `"anvil/planning"`
resolved to `openai/anvil/planning` (the whole string under the **default** provider) →
`model_not_found`, and the run fell back to `openai/gpt-5.5` / `gpt-5.4-mini` (also unknown) → failed.

**Fix (shipped):** emit `{ providerOverride: "anvil", modelOverride: "<preset>" }`. With the provider
named separately, all four turns completed and the capture endpoint recorded the **bare** preset:

| User turn | Plugin intent | Wire `model` captured |
|---|---|---|
| "Plan the migration across all services step by step" | `planning` | `planning` |
| "Fix the null pointer deref in handler.go" | `quick-edit` | `quick-edit` |
| "Review this pull request and find bugs" | `review` | `review` |
| "What is the capital of France" | `chat` | `chat` |

`validate.py --assert-wire-form --capture <capture>` → **PASS** (all four match `^(anvil/)?<preset>$`;
the front door also accepts both forms → correct tiers). The router's `parse_model` already strips an
optional `anvil/`, so the bare wire form is fully compatible.

### ✅ 2. Fire cadence — **exactly once per user message**
4 turns → 4 distinct `runId`s → **1 `before_model_resolve` fire each**. (An earlier run showed 2
fires/turn, but that was **fallback-retry noise**: a failed model resolution re-resolves and re-fires.
Once turns succeed, cadence is clean.) The "classify each turn" premise holds.
> Note: `validate.py --assert-fire-cadence` expects the **T013 logging-hook** log shape
> (`userMessageIndex`); the T014 plugin's decision log groups by `runId`, so cadence here was
> confirmed by `runId` count rather than that assertion. Follow-up: teach the assertion to accept
> `runId` as the per-message key (small).

### ✅ 3. `pluginApi` floor — **`2026.6.6` works**
The installed gateway is `2026.6.6`, newer than the plugin's `compat.pluginApi >= 2026.4.21` estimate.
`before_model_resolve`, `modelOverride`/`providerOverride`, `allowConversationAccess`, and the
`openclaw/plugin-sdk/plugin-entry` import the plugin uses are all present in the installed dist. The
`>=2026.4.21` floor is safe (not contradicted); a tighter floor can wait for the OpenClaw CHANGELOG.

## Two bugs the live run caught (that the synthetic fixture could not)
1. **Wrong wire form** — `modelOverride: "anvil/<preset>"` is mis-routed; needs `providerOverride` +
   bare model (above). The offline fixture passed because `WIRE_FORM_RE` accepts the namespaced form —
   it validated the *string*, not OpenClaw's *resolution* of it.
2. **Manifest missing `configSchema`** — OpenClaw `2026.6.6` **refuses to start** if a plugin manifest
   lacks `configSchema` (`Config validation failed: plugin manifest requires configSchema`). Added a
   minimal `{ "type": "object", "additionalProperties": false, "properties": {} }` to
   `openclaw.plugin.json`. Without it the gateway CLI won't load at all.

Both are fixed in the same change set as this doc; the regenerated `decision_log.fixture.jsonl` now
carries `providerOverride: "anvil"` + bare `modelOverride`.

## Operational notes (for re-running)
- TS-source plugins install only via `openclaw plugins install --link <path>` (a packaged install
  wants compiled `dist/index.js`); `--link` accepts the `.ts` source directly. `--force` is
  incompatible with `--link`.
- `openclaw agent` runs one turn through the gateway (fires the hook); the capture endpoint must be
  reachable from Fakoli-Mini (here `http://fakoli-dark:8000`, confirmed 200 both ways).
- The plugin's decision log defaults to `./decision_log.jsonl` relative to the **gateway service**
  CWD; set `ANVIL_DECISION_LOG` (or pin an absolute path) to read it reliably.
