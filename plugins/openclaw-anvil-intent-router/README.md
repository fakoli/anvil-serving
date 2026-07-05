# openclaw-anvil-intent-router — reference OpenClaw intent-router plugin (T014/T008)

The **reference** OpenClaw `before_model_resolve` plugin for anvil-serving.
On each turn it classifies the prompt (text + attachment kinds) into one of
anvil's closed presets, then applies an **upfront routing split** (T008):

- **Cloud-preferred classes** (default: `planning`) → **no override** → OpenClaw
  routes to the native subscription provider. Avoids a wasted anvil round-trip for
  classes eval-proven to work better on cloud models (T005 bake-off finding).
- **Local-preferred classes** (quick-edit, review, chat, long-context) → **anvil**
  → emits `{ providerOverride: "anvil", modelOverride: "<preset>" }`.

T008 is an **optimisation** (no wasted anvil round-trip for eval-proven-cloud
classes) — it does not itself change the M0 keyless-503 handoff design.

> **KNOWN DEFECT — read before relying on native failover
> (LIVE-CONFIRMED 2026-07-01).** The keyless-503 → `agents.defaults.model.fallbacks`
> safety net is **not reliable** for any turn where this plugin emitted
> `providerOverride:"anvil"` — i.e. every local-preferred-class turn
> (quick-edit / review / chat / long-context). Live E2E testing against a real
> OpenClaw gateway showed that after anvil 503s, OpenClaw's fallback walk
> (`agents.defaults.model.fallbacks`, e.g. `openai/gpt-5.5`, `openai/gpt-5.4-mini`)
> ALSO resolved through the `anvil` provider and 503'd again — the turn never
> reached the native cloud provider, and the user saw "couldn't generate a
> response". Root cause: `before_model_resolve` resolves its override once,
> "above the attempt loop" (source-confirmed,
> `docs/OPENCLAW-INTEGRATION-SPEC.md` §0), and that resolution appears to stick
> across the native-failover walk too. The safety net **is** reliable for
> cloud-preferred classes (`planning` by default) — no `providerOverride` is
> ever emitted for them, so there's nothing to stick.
>
> **This is an OpenClaw-side behavior; there is no known fix from this repo.**
> Two operator-side mitigations, in order of effort:
> 1. **`ANVIL_CLOUD_CLASSES`** — add any work-class whose local tier is known to
>    be flaky/exhausted to the cloud-preferred set (see "Cloud-class set" below).
>    That class's turns never touch anvil, so there's nothing for the failover
>    walk to inherit. Zero anvil-side config needed; costs the local-first
>    benefit for that class only.
> 2. **anvil's own opt-in metered cloud tier** (ADR-0001,
>    `configs/example-with-cloud.toml`) — add the at-risk work-classes to
>    `[router].metered_cloud` so anvil's `fallback.py` escalates to a bound
>    cloud tier **inside** the same `provider="anvil"` response. anvil never
>    returns 503 for those classes, so OpenClaw's (unreliable) native failover
>    is never invoked. This is a billing decision (ADR-0001) — opt in explicitly.
>
> Full root-cause writeup: `docs/OPENCLAW-INTEGRATION-SPEC.md`
> ("anvil-503 native-failover loop") and
> `docs/adr/0005-anvil-503-native-failover-unreliable.md`.

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

The decision log includes a `destination` field (`"anvil"` or `"native"`), an
`authoritative` flag, and a `routingSource` field. `authoritative` is true only
when `/v1/route` returns a valid tier; if the configured route endpoint is
unreachable, unauthorized, or malformed, the plugin falls back to the client-side
classifier and logs `routingSource: "client-side-fallback"`.

### Cloud-class set

```
Default: { "planning" }
```

Configure via `api.pluginConfig.cloudClasses` (the `cloudClasses` field in
`openclaw.plugin.json`'s config schema) or via the `ANVIL_CLOUD_CLASSES`
environment variable. The env var takes precedence when it is non-empty.
Either source replaces the default set entirely:

```bash
export ANVIL_CLOUD_CLASSES="planning,long-context"
```

Empty / whitespace-only values are treated as unset and fall through to the
next source, so an empty env var does not accidentally clear the configured
plugin set or the default.

### Native failover requirement (REQUIRED)

Because `planning` (and any other cloud-preferred class) is now routed to the
native provider upfront, the gateway MUST have `agents.defaults.model.fallbacks`
configured with the native provider — this is required for the upfront
(cloud-preferred) path, which IS reliable. It is **not** a reliable safety net
for anything that reaches anvil and exhausts (local-preferred classes) — see
the KNOWN DEFECT callout above:

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

Set `api.pluginConfig.routeEndpoint` or `ANVIL_ROUTE_ENDPOINT` to call anvil's
`POST /v1/route` (T007) as the **authoritative** tier decision instead of the
fast client-side heuristic. The env var takes precedence when it is non-empty:

```bash
export ANVIL_ROUTE_ENDPOINT="http://127.0.0.1:8000/v1/route"
```

If the route endpoint is protected by the router front-door token, provide the
token by env var name, not by raw value:

```bash
export ANVIL_ROUTE_AUTH_ENV="ANVIL_ROUTER_TOKEN"
export ANVIL_ROUTER_TOKEN="..."
```

`api.pluginConfig.routeAuthEnv` provides the same env-var-name setting when
`ANVIL_ROUTE_AUTH_ENV` is unset. The plugin sends both `Authorization: Bearer`
and `x-api-key` headers to match anvil-serving's accepted auth forms.

Trade-off:
- **Pro:** uses the router's full quality profile + config; catches edge cases
  the keyword classifier misses.
- **Con:** adds one loopback round-trip (~1–5 ms for co-located anvil; bounded
  by 30 ms default timeout). Falls back to client-side classify on any
  error/timeout — no run is ever broken.

**Default: client-side classify (no route endpoint configured).** Use the
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

> **OpenClaw >=2026.6.11: use `--link`, not a copy-install.** This plugin ships a
> compiled/TypeScript runtime (`index.ts` + `route.mjs`). Starting with OpenClaw
> 2026.6.11, the gateway's compiled-runtime loader rejects plugins installed by
> **copy** (`openclaw plugins install <path>` without a flag) — only a **linked**
> install is accepted. Always install with `--link`:

1. **Install the plugin** (dev/local, symlinked so gateway restarts pick up edits):
   ```bash
   openclaw plugins install --link ./plugins/openclaw-anvil-intent-router
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
   - `ANVIL_CLOUD_CLASSES` — comma-separated preset names to route to native;
     overrides `api.pluginConfig.cloudClasses`.
  - `ANVIL_ROUTE_ENDPOINT` — full URL of anvil's `/v1/route` (authoritative mode);
     overrides `api.pluginConfig.routeEndpoint`.
   - `ANVIL_ROUTE_AUTH_ENV` — env var name containing the optional `/v1/route`
     auth token, for example `ANVIL_ROUTER_TOKEN`; overrides
     `api.pluginConfig.routeAuthEnv`.
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
- `api.pluginConfig.cloudClasses` / `api.pluginConfig.routeEndpoint` fallback
  behavior, with env-var precedence
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

**2026-07-01 live E2E finding (new):** the keyless-503 → native-failover safety
net for local-preferred classes is **not reliable** — see the KNOWN DEFECT
callout near the top of this file, `docs/OPENCLAW-INTEGRATION-SPEC.md`
("anvil-503 native-failover loop"), and
`docs/adr/0005-anvil-503-native-failover-unreliable.md`.

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
