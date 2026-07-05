# Implementation plan — advise-and-defer (ADR-0001)

Realizes [ADR-0001](adr/0001-cloud-cost-and-subscription-auth.md): anvil = **local-serve + routing
brain**; the harness owns cloud on its subscription; **no cloud API key in the default path**. The
existing router-internal verify+fallback (PR #39) stays valid for the *opt-in keyed* case — this plan
makes the cloud tier optional, adds the keyless handoff, the decision endpoint, and the cost
dimension. Nothing here tears down what shipped.

> **Status update (2026-07-05):** the router-side keyless exhaustion contract shipped, but the
> OpenClaw-native handoff assumption in this early plan is superseded by
> [ADR-0005](adr/0005-anvil-503-native-failover-unreliable.md). OpenClaw's fallback walk can fire
> on anvil's exhaustion status, but if `before_model_resolve` pinned `providerOverride:"anvil"`, the
> fallback attempts can remain pinned to that provider. Treat Phase 1 below as the router guarantee
> only; use cloud-preferred upfront routing or anvil's opt-in metered cloud tier for OpenClaw
> local-preferred turns.

**Sequencing note:** Phase 0 + Phase 1 are the launch-relevant core (they make the shipped default
keyless and prove the handoff). Phases 2–5 are incremental and can land after the public flip.

---

## Phase 0 — Config: cloud opt-in + cost + per-intent metering  *(foundation)*

**Goal:** the default config is local-only; a cloud tier is something an operator explicitly adds,
explicitly meters, and explicitly maps to intents.

- `config.py` `Tier`: add optional `cost_input_per_mtok` / `cost_output_per_mtok` (USD per **million**
  tokens; `None` = unknown), and keep `privacy=="cloud"` as the metered marker. `_parse_tier` parses +
  validates them (floats ≥ 0 or absent).
- `config.py` `RouterConfig`: add an explicit **metered-intent list** — e.g. `[router] metered_cloud =
  ["planning"]` — listing the intents/work-classes permitted to use a metered cloud tier. **Empty/absent
  by default.** A cloud tier is only ever a candidate for an intent that appears here.
- Ship `configs/example.toml` as **local-only** (no cloud tier). Add `configs/example-with-cloud.toml`
  showing the opt-in cloud tier + `metered_cloud` list + cost fields, with a loud "this is metered $"
  comment.
- `policy.route()` already filters candidates; add the metered-intent gate so a cloud tier is dropped
  from the candidate pool for any work-class not in `metered_cloud`.

**Tests:** default config loads with zero cloud tiers; a cloud tier present but the work-class not in
`metered_cloud` → cloud is not a candidate; a mapped work-class → cloud is a candidate. **Blocking for
the public flip** (so the shipped default is keyless).

---

## Phase 1 — Keyless fallback handoff  *(the keystone; must live-validate)*

**Goal:** in the local-only default, an `allow-with-verify` miss cleanly returns a gateway handoff
signal with no partial local tokens. OpenClaw native-provider recovery is caveated by ADR-0005 when a
plugin-pinned `providerOverride` reached anvil first.

- Confirm `route_with_fallback` (`fallback.py`) on exhaustion (no remaining tier) raises
  `NoAvailableTierError`, and `front_door.py` maps that to a **503** — *already the behavior*. The
  commit-window (`commit_window.py`) guarantees nothing local was streamed before the 503 (C3).
- **LIVE-VALIDATED WITH CAVEAT:** against OpenClaw **2026.6.6**, anvil's exhaustion status can trigger
  OpenClaw's overloaded/transport fallback category, but ADR-0005 proved the native fallback is not a
  reliable escape when the failing attempt was resolved through `providerOverride:"anvil"`.
- Make the exhaustion status + body explicit and documented (it is now a *contract* with the gateway,
  not an internal detail).

**Tests:** local-only config + `allow-with-verify` whose local output fails verify → 503, no local
token in the body (extend `test_serve_verify_fallback.py` for the keyless/no-cloud-tier path). Plus the
live OpenClaw validation (manual, documented). **Blocking for the public flip.**

---

## Phase 2 — Cost dimension + optional cost-sync

**Goal:** when a metered cloud tier is used, the $ cost is visible; prices can optionally be refreshed
from a free source.

- `decision_log.py` / `metrics.py`: when a request is served by (or routed to) a metered cloud tier,
  compute estimated cost from the tier's cost fields × token counts; surface it in the decision record
  + a `cost_usd` metric. Local tiers report `0`.
- **Optional cost-sync (off by default):** a new `eval`/`models`-style subcommand or `[routing]
  cost_sync = true` toggle that does a stdlib `urllib` GET of the LiteLLM pricing JSON
  (`raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`, MIT), caches
  it at `~/.cache/anvil-serving/prices.json` with a 24 h TTL, normalizes the model key
  (bare/`anthropic/`), and fills any tier cost fields left unset. Falls back to static config on any
  failure or when disabled. No runtime dependency, no key. (Runner-up source if LiteLLM's shape churns:
  `pydantic/genai-prices` `data_slim.json`, already per-million.)

**Tests:** cost surfaced for a metered cloud route, `0` for local; cost-sync parses a fixture pricing
JSON and maps a Claude/GPT id → cost; sync disabled → static config used; fetch failure → graceful
fallback. **Incremental.**

---

## Phase 3 — `POST /v1/route` decision endpoint

**Goal:** expose the routing brain standalone (for the plugin + non-OpenClaw harnesses) — research
shows this is novel (NotDiamond `select_model()` is the only precedent), so we define the contract.

- `front_door.py`: add a `POST /v1/route` route. It runs `intent.resolve` + `policy.route` (the same
  brain as the serve path) but **does not serve** — returns the decision.
- Request: a `/v1/chat/completions`-shaped body, plus optional `signals` `{work_class, token_estimate,
  urgency}` (if absent, infer from `messages` + `max_tokens`).
- Response: `{ tier: "local"|"cloud", model, provider, work_class, reason, confidence, session_id }`.
  Status: 200 (decision, even if `cloud`), 400 (malformed), 503 (no suitable tier).
- `discovery.py`: advertise `/v1/route` where it advertises the preset vocabulary.

**Tests:** `/v1/route` returns a well-formed decision for allow / allow-with-verify / deny / metered
intents; never triggers a backend call; malformed → 400. **Incremental.**

---

## Phase 4 — Plugin routing (OpenClaw `before_model_resolve`)

**Goal:** the plugin does the coarse upfront split so anvil only sees what it should serve.

- `plugins/openclaw-anvil-intent-router/`: in `before_model_resolve`, classify client-side (the shared
  `tier0_keywords.json` vocab) and route **`deny`-class → the native provider directly** (no anvil
  round-trip); **`allow` / `allow-with-verify` → anvil**. Optionally call anvil's `POST /v1/route`
  instead of embedding the classifier, to keep one source of routing truth (trade-off: a round-trip vs
  client-side duplication — keep the deterministic client classifier as the fast path, `/v1/route` as
  the authoritative override).
- Configure cloud-preferred presets to route directly to the native provider/model. Optional
  `agents.defaults.model.fallbacks` may still help native-provider transport failures, but must not
  be presented as a reliable rescue for local-preferred anvil 503s; see ADR-0005.

**Tests:** plugin unit tests for the routing split; the keyword-parity test already guards classifier
drift. **Incremental.**

---

## Phase 5 — Docs & contract reshape

- **Contract C4** (verify-and-fallback): reframe to two modes — *keyless* (exhaustion-503 → gateway
  transport failover) and *opt-in keyed* (router-internal escalation → 200). Update
  `docs/QUALITY-GATED-ROUTER.md`.
- `README.md`: state the default is **local-only, no metered cloud**; document the opt-in metered cloud
  tier with a prominent "this incurs metered API billing" callout + the per-intent mapping.
- `CLAUDE.md`: note the cloud tier is opt-in/off-by-default and the Agent-SDK golden rule already in place.
- Cross-link ADR-0001.

---

## Open / to-confirm during build
- OpenClaw failover trigger status is no longer unknown: ADR-0005 records the live caveat. Future work
  is an OpenClaw-side fix or a confirmed provider-resolution change that lets fallback attempts escape
  a hook-emitted `providerOverride`.
- Whether the plugin embeds the classifier or calls `/v1/route` (Phase 4) — decide after `/v1/route` lands.
- A follow-up ADR if the optional cost-sync grows beyond a single static-JSON fetch.
