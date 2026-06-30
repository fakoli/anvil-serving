# openclaw-anvil-intent-router — reference OpenClaw intent-router plugin (T014/T008)

The **reference** OpenClaw `before_model_resolve` plugin for anvil-serving.
On each turn it classifies the prompt (text + attachment kinds) into one of
anvil's closed presets, then applies an **upfront routing split** (T008):

- **Cloud-preferred classes** (default: `planning`) → **no override** → OpenClaw
  routes to the native subscription provider. Avoids a wasted anvil round-trip for
  classes eval-proven to work better on cloud models (T005 bake-off finding).
- **Local-preferred classes** (quick-edit, review, chat, long-context) → **anvil**
  → emits `{ providerOverride: "anvil", modelOverride: "<preset>" }`.

The existing keyless-503 → native failover (`agents.defaults.model.fallbacks`)
remains the safety net for anything that reaches anvil and exhausts.  T008 is an
**optimisation** — no design change to the M0 guarantee.

> **Focus, not couple.** All OpenClaw-specific code lives in this swappable
> adapter package. The router core (`anvil_serving/router/`) contains **zero**
> OpenClaw references (AC2) — if the hook API churns, only this plugin changes.

## Files

| File | What it is |
|------|------------|
| `index.ts` | The plugin hook. `definePluginEntry` + `api.on("before_model_resolve", ...)`; classifies, applies routing split (T008), writes a decision-log line. |
| `classify.mjs` | The SINGLE SOURCE OF TRUTH heuristic (`classify`, `PRESETS`). Imported by `index.ts`, `make-fixture.mjs`, and `test.mjs`. |
| `classify.d.mts` | TypeScript declarations for `classify.mjs`. |
| `route.mjs` | T008 routing decision layer: `makeRoutingDecision`, `getCloudClasses`, `fetchAnvilTier`. Pure ESM, no OpenClaw dependency, directly testable. |
| `route.d.mts` | TypeScript declarations for `route.mjs`. |
| `test.mjs` | `node --test` unit tests for the routing split (no gateway required). |
| `package.json`, `openclaw.plugin.json` | Plugin packaging + config schema. |
| `make-fixture.mjs` | Regenerates `decision_log.fixture.jsonl` from the real `classify`. |
| `decision_log.fixture.jsonl` | **SYNTHETIC fixture — not a live capture.** See "The committed fixture is SYNTHETIC" below. |
| `tier0_keywords.json` | Byte-identical bundled copy of `anvil_serving/router/tier0_keywords.json`. |

## Routing split (T008)

### Why

`planning` is the only preset where the local 35B-A3B MoE tier measured
measurably weaker than the cloud subscription tier (T005 bake-off, multi-step
decomposition / long-horizon planning).  For all other presets, local hardware
performs adequately at the measured request distribution.

Before T008, the plugin sent *every* turn to anvil, and anvil's local-only
default returned 503 for `planning` → OpenClaw failover → native provider.
That worked but wasted a round-trip.  T008 short-circuits that path.

### How

`before_model_resolve` now:
1. Classifies the prompt via `classify.mjs`.
2. Calls `makeRoutingDecision(preset, cloudClasses)` from `route.mjs`.
3. If the preset is in `cloudClasses` → returns `{}` (no override; native
   provider resolves normally via `agents.defaults.model.primary`).
4. Otherwise → returns `{ providerOverride: "anvil", modelOverride: "<preset>" }`.

The decision log now includes a `destination` field (`"anvil"` or `"native"`)
and an `authoritative` flag (true when the `/v1/route` mode is active).

### Cloud-class set

```
Default: { "planning" }
```

Extend via the `ANVIL_CLOUD_CLASSES` environment variable (comma-separated
preset names; replaces the default entirely):

```bash
export ANVIL_CLOUD_CLASSES="planning,long-context"
```

The `configSchema.cloudClasses` field in `openclaw.plugin.json` documents the
same knob for the gateway's plugin config UI (the env var takes precedence).

### Native failover requirement (REQUIRED)

Because `planning` (and any other cloud-preferred class) is now routed to the
native provider upfront, the gateway MUST have `agents.defaults.model.fallbacks`
configured with the native provider — **both** for this upfront path and as the
M0 keyless-503 safety net for anything that reaches anvil and exhausts:

```jsonc
// ~/.openclaw/openclaw.json
agents: {
  defaults: {
    model: {
      primary: "anvil/chat",          // default when no cloud class matched
      fallbacks: ["anthropic/claude-sonnet-4-5"]  // native provider (required)
    }
  }
}
```

Without `fallbacks`, a cloud-class request that routes to the native provider
can fail silently if the native model is not in the catalog.

### Optional authoritative mode: POST /v1/route (T007)

Set `ANVIL_ROUTE_ENDPOINT` to call anvil's `POST /v1/route` (T007) as the
**authoritative** tier decision instead of the fast client-side heuristic:

```bash
export ANVIL_ROUTE_ENDPOINT="http://127.0.0.1:8000/v1/route"
```

Trade-off:
- **Pro:** uses the router's full quality profile + config; catches edge cases
  the keyword classifier misses.
- **Con:** adds one loopback round-trip (~1–5 ms for co-located anvil; bounded
  by 30 ms default timeout). Falls back to client-side classify on any
  error/timeout — no run is ever broken.

**Default: client-side classify (ANVIL_ROUTE_ENDPOINT unset).** Use the
authoritative mode only when anvil is co-located (loopback or LAN) and the
extra classification accuracy outweighs the latency.

## Classification → routing table

`classify(prompt, attachments)` is deterministic, word-boundary keyword
matching (intent-first, NOT substring), over prompt text + attachment kinds
only.

| # | Signal | Preset | Route destination |
|---|--------|--------|-------------------|
| 1 | very long prompt (>= 24,000 chars) | `long-context` | **anvil** |
| 2 | many attachments (>= 4) | `long-context` | **anvil** |
| 3 | any **media** attachment (image/video/audio/document) | `review` | **anvil** |
| 4 | `review` / `critique` / `feedback` / `audit` | `review` | **anvil** |
| 5 | `plan` / `plans` / `planning` / `design` / `architect` / `decompose` / `roadmap` / `break down` / `step by step` | `planning` | **native** (cloud-preferred) |
| 6 | `refactor` / `rename across` / `across the codebase` / `migrate the` | `review` | **anvil** |
| 7 | `edit` / `fix` / `change` / `implement` / `patch` / `add a` / `update the` | `quick-edit` | **anvil** |
| 8 | (default) | `chat` | **anvil** |

> **Single taxonomy.** Keyword phrase sets and precedence are a 1:1 mirror of
> `anvil_serving/router/classify.py` (`_KEYWORD_PHRASES`), loaded from the
> bundled `tier0_keywords.json` copy. `tests/router/test_keyword_parity.py`
> fails loudly if the two copies drift.

## Install (on the OpenClaw gateway — e.g. Fakoli Mini)

1. **Install the plugin** (dev/local):
   ```bash
   openclaw plugins install ./plugins/openclaw-anvil-intent-router
   ```
2. **Grant conversation access — REQUIRED.** Any non-bundled plugin using
   `before_model_resolve` must be granted conversation access in
   `~/.openclaw/openclaw.json`:
   ```jsonc
   plugins: {
     entries: {
       "openclaw-anvil-intent-router": { hooks: { allowConversationAccess: true } }
     }
   }
   ```
   (This hook does **not** mutate the prompt, so it does **not** need
   `allowPromptInjection`.)
3. **Register the anvil provider — REQUIRED.** The plugin returns
   `{ providerOverride: "anvil", modelOverride: "<preset>" }` for local classes,
   so `~/.openclaw/openclaw.json` **MUST** define the `anvil` provider with
   `models[]` entries for **every** preset id (`planning`, `quick-edit`, `review`,
   `chat`, `long-context`). Full recipe: `docs/OPENCLAW-INTEGRATION-SPEC.md §2`.
   ```jsonc
   models: {
     mode: "merge",
     providers: {
       anvil: {
         baseUrl: "http://127.0.0.1:8000/v1",   // anvil-serving front door (loopback)
         api: "openai-completions",
         models: [
           { id: "planning",     name: "Anvil · Planning" },
           { id: "quick-edit",   name: "Anvil · Quick Edit" },
           { id: "review",       name: "Anvil · Review",       input: ["text", "image"] },
           { id: "chat",         name: "Anvil · Chat" },
           { id: "long-context", name: "Anvil · Long Context" }
         ]
       }
     }
   }
   ```
   > **LIVE-CONFIRMED (OpenClaw 2026.6.6, Fakoli-Mini, 2026-06-30).** The plugin
   > names the provider (`providerOverride: "anvil"`) and emits the **bare** preset
   > (`modelOverride: "planning"`); OpenClaw forwards the bare id on the wire. A
   > lone `modelOverride: "anvil/<preset>"` is mis-resolved → `model_not_found`.
4. **Configure native failover — REQUIRED for cloud-class routing (T008).**
   Local classes resolve to the native provider via no-override. The gateway
   MUST have a fallback configured so `planning` (and any other cloud class) can
   resolve to a real native model:
   ```jsonc
   agents: {
     defaults: {
       model: {
         primary: "anvil/chat",
         fallbacks: ["anthropic/claude-sonnet-4-5"]
       }
     }
   }
   ```
5. **Restart the gateway:** `openclaw gateway restart`.
6. **(Optional) set environment variables:**
   - `ANVIL_CLOUD_CLASSES` — comma-separated preset names to route to native.
   - `ANVIL_ROUTE_ENDPOINT` — full URL of anvil's `/v1/route` (authoritative mode).
   - `ANVIL_DECISION_LOG` — absolute path for the decision log (defaults to
     `./decision_log.jsonl` relative to the gateway's CWD).

## Running tests (no gateway required)

```bash
cd plugins/openclaw-anvil-intent-router
node --test test.mjs
```

Or via npm:
```bash
npm test
```

Tests cover:
- `planning` prompt → `planning` preset → `{}` (native, no anvil contact)
- `quick-edit` / `review` / `chat` prompts → correct wire form `{ providerOverride:"anvil", modelOverride:"<preset>" }`
- `ANVIL_CLOUD_CLASSES` env var override
- Wire-form assertion: `modelOverride` is bare preset, never `"anvil/<preset>"`

## LIVE validation (PENDING — T008)

> **Status: pending live-gateway confirmation.** The upfront routing split
> requires a real OpenClaw 2026.6.6 gateway on Fakoli Mini to confirm that:
> 1. A `planning` turn returns `{}` from the plugin and OpenClaw uses its
>    native provider (NOT anvil).
> 2. A `quick-edit` / `review` / `chat` turn returns
>    `{ providerOverride:"anvil", modelOverride:"<preset>" }` and OpenClaw
>    routes to the anvil endpoint.
> 3. The decision log's `destination` field correctly records `"native"` vs
>    `"anvil"` for each turn.
>
> This shares T005's gateway dependency (Fakoli Mini). The plugin tests above
> cover the routing logic without a gateway. Live confirmation is the remaining
> integration step.

The prior wire-form and fire-cadence gaps are already settled
(`docs/findings/2026-06-30-openclaw-live-validation.md`).

## LIVE integration step (MANUAL — run by a human on the gateway)

> This is the live half of AC1, separately labeled from the committed synthetic
> fixture.  Requires the real OpenClaw install on Fakoli Mini.

1. Install + gate + register provider + configure fallbacks (steps above); restart.
2. **Send a planning turn** (cloud-preferred, should NOT reach anvil):
   ```bash
   openclaw agent -m "Plan the migration across services step by step"
   ```
   Assert the decision log shows `destination: "native"`:
   ```bash
   jq -e 'select(.source=="openclaw" and .intent=="planning" and .destination=="native")' \
      decision_log.jsonl
   ```
   Confirm anvil-serving access log shows **no** `planning` request.
3. **Send a quick-edit turn** (should reach anvil):
   ```bash
   openclaw agent -m "Fix the null pointer deref in handler.go"
   ```
   Assert the decision log shows `destination: "anvil"`:
   ```bash
   jq -e 'select(.source=="openclaw" and .intent=="quick-edit" and .destination=="anvil")' \
      decision_log.jsonl
   ```
   Confirm the anvil-serving access log shows `model: "quick-edit"`.
4. **(Optional) wire-form check:**
   ```bash
   python examples/openclaw/validate.py --assert-wire-form --capture decision_log.jsonl
   ```

## The committed fixture is SYNTHETIC

`decision_log.fixture.jsonl` is a **synthetic, regenerable** stand-in for a live
decision log — every line carries `"synthetic": true`. It exists so AC1 can be
asserted in CI without a live gateway. It is produced by `make-fixture.mjs`,
which imports the **same** `classify` AND the **same** routing-decision layer
(`route.mjs`) the plugin runs, so the fixture is provably the plugin's real
output — including the T008 split (planning → `destination:"native"`,
`providerOverride:null`; local presets → `destination:"anvil"` + bare override).
Regenerate it any time with:

```bash
node plugins/openclaw-anvil-intent-router/make-fixture.mjs
```

AC1 (synthetic half) is asserted exactly as the live step, against the fixture —
note the T008 invariant: a `planning` turn is routed to the **native** provider
(`destination:"native"`), NOT to anvil:

```bash
jq -e 'select(.source=="openclaw" and .intent=="planning" and .destination=="native")' \
   plugins/openclaw-anvil-intent-router/decision_log.fixture.jsonl
```
