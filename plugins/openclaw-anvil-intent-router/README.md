# openclaw-anvil-intent-router — reference OpenClaw intent-router plugin (T014/T008)

The **reference** OpenClaw `before_model_resolve` plugin for anvil-serving.
On each turn it classifies the prompt (text + attachment kinds) into one of
anvil's closed presets, then applies an **upfront routing split** (T008):

- **Cloud-preferred presets** (default: `planning`) → an explicit native provider/model
  override. The package retains `anthropic/claude-sonnet-4-5` only as a compatibility fallback;
  production setup must select the gateway's real native provider/model. Avoids a wasted anvil
  round-trip for presets eval-proven to work better on cloud models (T005 bake-off finding).
- **Local-preferred presets** (quick-edit, review, chat, long-context) → **anvil**
  → emits `{ providerOverride: "anvil", modelOverride: "<preset>" }`.

T008 is an **optimisation** (no wasted anvil round-trip for eval-proven-cloud
classes) — it does not itself change the M0 keyless-503 handoff design.

> **KNOWN DEFECT — read before relying on native failover
> (LIVE-CONFIRMED 2026-07-01).** The keyless-503 → `agents.defaults.model.fallbacks`
> safety net is **not reliable** for any turn where this plugin emitted
> `providerOverride:"anvil"` — i.e. every local-preferred preset turn
> (quick-edit / review / chat / long-context). Live E2E testing against a real
> OpenClaw gateway showed that after anvil 503s, OpenClaw's fallback walk
> (`agents.defaults.model.fallbacks`, e.g. `openai/gpt-5.5`, `openai/gpt-5.4-mini`)
> ALSO resolved through the `anvil` provider and 503'd again — the turn never
> reached the native cloud provider, and the user saw "couldn't generate a
> response". Root cause: `before_model_resolve` resolves its override once,
> "above the attempt loop" (source-confirmed,
> `docs/OPENCLAW-INTEGRATION-SPEC.md` §0), and that resolution appears to stick
> across the native-failover walk too. Cloud-preferred presets (`planning` by
> default) avoid that path by routing directly to the configured native provider
> instead of touching anvil.
>
> **This is an OpenClaw-side behavior; there is no known fix from this repo.**
> Two operator-side mitigations, in order of effort:
> 1. **`ANVIL_CLOUD_CLASSES`** — add any preset whose local tier is known to
>    be flaky/exhausted to the cloud-preferred set (see "Cloud-class set" below).
>    That preset's turns never touch anvil, so there's nothing for the failover
>    walk to inherit. Zero anvil-side config needed; costs the local-first
>    benefit for that class only.
> 2. **anvil's own opt-in metered cloud tier** (ADR-0001,
>    `configs/example-with-cloud.toml`) — add the at-risk work-classes to
>    `[router].metered_cloud` so anvil's `fallback.py` escalates to a bound
>    cloud tier **inside** the same `provider="anvil"` response. anvil never
>    returns 503 for those classes, so OpenClaw's (unreliable) native failover
>    is never invoked. This is a billing decision (ADR-0001) — opt in explicitly.
>
> Full root-cause writeup: `docs/OPENCLAW-INTEGRATION-SPEC.md` §7
> ("native fallback does not escape the override") and
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
That worked only where gateway failover escaped the anvil provider. T008
short-circuits that path.

### How

`before_model_resolve` now:
1. Classifies the prompt via `classify.mjs`.
2. Calls `makeRoutingDecision(preset, cloudClasses, nativeRoute)` from `route.mjs`.
3. If the preset is in `cloudClasses` → returns the configured native route, for example
   `{ providerOverride: "anthropic", modelOverride: "claude-sonnet-4-5" }`.
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

### Native route requirement (REQUIRED)

Because `planning` (and any other cloud-preferred preset) is now routed to the
native provider upfront, the plugin needs an explicit native provider/model. The
package fallback is not a deployment default; set plugin config or env vars to the
gateway's actual native route. Keep `agents.defaults.model.primary` native as a safe
no-plugin fallback:

```jsonc
// ~/.openclaw/openclaw.json
plugins: {
  entries: {
    "openclaw-anvil-intent-router": {
      hooks: { allowConversationAccess: true },
      config: {
        nativeProvider: "openai",
        nativeModel: "gpt-5.6-sol",
        routeTimeoutMs: 30
      }
    }
  }
}
```

Optional `agents.defaults.model.fallbacks` may still help native-provider
transport failures, but it is **not** a reliable safety net for anything that
reaches anvil and exhausts (local-preferred presets) — see the KNOWN DEFECT
callout above.

### Optional authoritative mode: POST /v1/route (T007)

Set `api.pluginConfig.routeEndpoint` or `ANVIL_ROUTE_ENDPOINT` to call anvil's
`POST /v1/route` (T007) as the **authoritative** tier decision instead of the
fast client-side heuristic. The env var takes precedence when it is non-empty:

```bash
export ANVIL_ROUTE_ENDPOINT="http://127.0.0.1:8000/v1/route"
```

For split-host deployments where the OpenClaw gateway is on Fakoli Mini and
anvil-serving is on the GPU/router host, use the router host's private/tailnet
address instead. `127.0.0.1` is same-host only from the gateway's perspective.

If the route endpoint is protected by the router front-door token, provide the
token by env var name, not by raw value:

```bash
export ANVIL_ROUTE_AUTH_ENV="ANVIL_ROUTER_TOKEN"
export ANVIL_ROUTER_TOKEN="..."
```

`api.pluginConfig.routeAuthEnv` provides the same env-var-name setting when
`ANVIL_ROUTE_AUTH_ENV` is unset. The plugin sends both `Authorization: Bearer`
and `x-api-key` headers to match anvil-serving's accepted auth forms.
Set `routeTimeoutMs` or `ANVIL_ROUTE_TIMEOUT_MS` when the route endpoint crosses
a tailnet; values are bounded to 1-5000 ms and default to 30 ms.

Trade-off:
- **Pro:** uses the router's full quality profile + config; catches edge cases
  the keyword classifier misses.
- **Con:** adds one loopback round-trip (~1–5 ms for co-located anvil; bounded
- **Con:** adds one controller round-trip (~1-5 ms for same-host anvil; bounded
  by 30 ms default timeout). A route-endpoint 503 is treated as a native/cloud
  decision; other errors/timeouts fall back to client-side classify - no run is
  ever broken.

**Default: client-side classify (no route endpoint configured).** Use the
authoritative mode when the extra classification accuracy outweighs the latency;
for split-host/Tailscale endpoints, raise `routeTimeoutMs` instead of relying on
the 30 ms same-host default.

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

For a complete provider + plugin + route + tool-policy setup, prefer the owning harness command:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --native-provider openai \
  --native-model gpt-5.6-sol \
  --plugin-dir /absolute/path/to/openclaw-anvil-intent-router \
  --tool-profile full \
  --exec-mode auto \
  --out ~/.openclaw/openclaw.json \
  --restart
```

Fresh config writes are refused when the native route or absolute plugin path is missing. The
manual steps below remain useful for registry-managed or custom installs.

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
   `{ providerOverride: "anvil", modelOverride: "<preset>" }` for local presets,
   so `~/.openclaw/openclaw.json` **MUST** define the `anvil` provider with
   `models[]` entries for **every** local preset id (`planning`, `quick-edit`, `review`,
   `chat`, `chat-fast`, `long-context`). `chat-fast` is not emitted by the automatic
   classifier today, but `harness sync` renders every router preset so it remains available
   for manual selection. Full recipe: `docs/OPENCLAW-INTEGRATION-SPEC.md §2`.
   ```jsonc
   models: {
     mode: "merge",
     providers: {
      anvil: {
        baseUrl: "http://anvil-gpu.tailnet.example:8000/v1", // split-host gateway -> router host
        // For same-host development only, use http://127.0.0.1:8000/v1 instead.
        apiKey: "${ANVIL_ROUTER_TOKEN}",
        api: "openai-completions",
         models: [
           { id: "planning",     name: "Anvil · Planning" },
           { id: "quick-edit",   name: "Anvil · Quick Edit" },
           { id: "review",       name: "Anvil · Review",       input: ["text", "image"] },
           { id: "chat",         name: "Anvil · Chat" },
           { id: "chat-fast",    name: "Anvil · Chat Fast" },
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

   When OpenClaw has already resolved an Anvil model for a trusted runtime path,
   the plugin honors that context instead of reclassifying the prompt. The main
   example is Anvil Voice Talk consults with `talk.consultModel:
   "anvil/chat-fast"`: the hook resolves `chat-fast`, returns the bare
   `modelOverride`, and marks the decision source as `openclaw-context`.
   The Anvil Serving voice sync also renders `talk.consultThinkingLevel: "off"`
   and `talk.consultBootstrapContextMode: "lightweight"` by default so those
   consults do not inherit stale low-reasoning latency or inject large workspace
   bootstrap files into each spoken turn.
4. **Configure the native route — REQUIRED for cloud-preferred preset routing (T008).**
   Cloud-preferred presets use this explicit provider/model instead of falling
   through to `agents.defaults.model.primary`:
   ```jsonc
   plugins: {
     entries: {
       "openclaw-anvil-intent-router": {
         hooks: { allowConversationAccess: true },
         config: {
           nativeProvider: "openai",
           nativeModel: "gpt-5.6-sol"
         }
       }
     }
   }
   ```
   Keep `agents.defaults.model.primary` on a real native model so a missing or disabled plugin
   fails safe instead of forcing every uncaught turn through Anvil.
5. **Restart the gateway:** `openclaw gateway restart`.
6. **(Optional) set environment variables:**
   - `ANVIL_CLOUD_CLASSES` — comma-separated preset names to route to native;
     overrides `api.pluginConfig.cloudClasses`.
   - `ANVIL_ROUTE_ENDPOINT` — full URL of anvil's `/v1/route` (authoritative mode);
     overrides `api.pluginConfig.routeEndpoint`.
   - `ANVIL_ROUTE_AUTH_ENV` — env var name containing the optional `/v1/route`
     auth token, for example `ANVIL_ROUTER_TOKEN`; overrides
     `api.pluginConfig.routeAuthEnv`.
   - `ANVIL_ROUTE_TIMEOUT_MS` — timeout for authoritative `/v1/route` probes;
     overrides `api.pluginConfig.routeTimeoutMs`.
   - `ANVIL_NATIVE_PROVIDER` / `ANVIL_NATIVE_MODEL` — native OpenClaw route for
     cloud-preferred presets; overrides plugin config.
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
- `planning` prompt → `planning` preset → explicit native provider/model override
- `quick-edit` / `review` / `chat` prompts → correct wire form `{ providerOverride:"anvil", modelOverride:"<preset>" }`
- `ANVIL_CLOUD_CLASSES` env var override
- `api.pluginConfig.cloudClasses` / `api.pluginConfig.routeEndpoint` fallback
  behavior, with env-var precedence
- Wire-form assertion: `modelOverride` is bare preset, never `"anvil/<preset>"`

## Release smoke/eval

After changing plugin routing behavior, OpenClaw provider config, router
presets, or a tier/model recipe, run the COLO smoke/eval from the repo root.
The plugin should stay a thin intent adapter; benchmark dimensions live in the
router config tier `params`, not in plugin constants.

```bash
python examples/openclaw/colo_smoke.py \
  --live \
  --gateway-host fakoli-mini \
  --router-base-url http://100.87.34.66:8000/v1 \
  --run-generations \
  --run-interaction-benchmark \
  --artifact .anvil/evidence/openclaw-colo-live-interactions.json \
  --pretty
```

The repeatable interaction benchmark launches direct-router prompts from the
OpenClaw gateway host. It records route provider/model from companion
`/v1/route` probes, exact usage tokens, streaming TTFT, latency, finish reasons,
and the applied recipe for `chat-fast`, `quick-edit`, `review`, `planning`, and
`long-context`. It validates gateway-to-router reachability and router behavior;
it does not by itself prove OpenClaw's full provider attempt loop. For
publishable numbers, create or update a findings note under `docs/findings/`;
the current live citation is
`docs/findings/2026-07-07-openclaw-colo-interaction-benchmark.md`.

## LIVE validation

> **Status: core wire/cadence behavior is live-confirmed.** The live validation record confirmed the
> bare-preset wire form and the once-per-run hook cadence. The upfront routing split is covered by
> unit tests and should be smoke-checked again when upgrading OpenClaw or changing provider config:
> 1. A `planning` turn returns the configured native provider/model override from the plugin (NOT anvil).
> 2. A `quick-edit` / `review` / `chat` turn returns
>    `{ providerOverride:"anvil", modelOverride:"<preset>" }` and OpenClaw routes to the anvil endpoint.
> 3. The decision log's `destination` field correctly records `"native"` vs `"anvil"` for each turn.

The prior wire-form and fire-cadence gaps are already settled; see
`docs/OPENCLAW-INTEGRATION-SPEC.md`, `docs/adr/0005-anvil-503-native-failover-unreliable.md`, and
the public `docs/findings/2026-07-04-openclaw-keyless-failover.md` evidence snapshot.

**2026-07-01 live E2E finding (new):** the keyless-503 → native-failover safety
net for local-preferred presets is **not reliable** — see the KNOWN DEFECT
callout near the top of this file, `docs/OPENCLAW-INTEGRATION-SPEC.md` §7
("native fallback does not escape the override"), and
`docs/adr/0005-anvil-503-native-failover-unreliable.md`.

## LIVE integration step (MANUAL — run by a human on the gateway)

> This is the live half of AC1, separately labeled from the committed synthetic
> fixture.  Requires the real OpenClaw install on Fakoli Mini.

1. Install + gate + register provider + configure the native route (steps above); restart.
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
output — including the T008 split (planning → `destination:"native"` with the
configured native provider/model; local presets → `destination:"anvil"` + bare override).
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
