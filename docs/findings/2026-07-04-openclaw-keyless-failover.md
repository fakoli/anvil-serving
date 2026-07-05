# Finding — OpenClaw keyless failover: does anvil's exhaustion-503 hand off to the native subscription? (advise-and-defer:T005)

- **Date:** 2026-07-04 (consolidating the 2026-07-01 live validation)
- **Task:** `advise-and-defer:T005` — "Live-validate the OpenClaw failover trigger (resolve the ADR-0001 UNCONFIRMED)"
- **Resolves:** the `Must validate live (currently UNCONFIRMED)` item in
  [ADR-0001 §Mechanism](../adr/0001-cloud-cost-and-subscription-auth.md)
- **Related:** [ADR-0005](../adr/0005-anvil-503-native-failover-unreliable.md) (the reliability caveat),
  `docs/OPENCLAW-LIVE-VALIDATION.md` Gap 4, `docs/PLAN-advise-and-defer.md` Phase 1

## The question

anvil's keyless (local-only, no cloud API key) default returns a **503 with zero streamed local
tokens** when it exhausts its local candidate tiers. ADR-0001 designed the keyless handoff around a
single unverified assumption: that OpenClaw's own **native transport failover**
(`agents.defaults.model.fallbacks`, which fires on auth/429/**overloaded**/timeout/billing) classifies
that 503 as "overloaded" and re-runs the request on the operator's native cloud subscription — with **no
cloud key ever touching anvil**. T005 had to confirm this live and record the confirmed status.

## Confirmed answer

**`exhaustion_status = 503` is correct and confirmed** for OpenClaw 2026.6.x. The exhaustion-503 **does**
trip OpenClaw's "overloaded" transport-failover category — **with one documented reliability caveat**
(the `providerOverride` loop, below).

### Router side — the mechanism (implemented in advise-and-defer:T004; re-verified 2026-07-04)

The serve path returns the **operator-configurable** `exhaustion_status` on exhaustion, defaulting to
503, with **C3** preserved (no partial local tokens streamed before the status):

- **Config knob:** `RouterConfig.exhaustion_status: int = 503`
  (`anvil_serving/router/config.py:149`), parsed from `[router].exhaustion_status` and validated to an
  int in 100–599 (`config.py:553-563`); threaded to the front door at `serve.py:958-960`.
- **Emission:** `FrontDoorHandler._no_tier_response()` passes the configured status to `_error(...)`,
  rendered in the caller's dialect (`front_door.py:186-192`). OpenAI body:
  `{"error":{"type":"service_unavailable","message":"no quality-gated tier is available for this request"}}`.
- **Trigger:** `RoutingBackend.generate()` raises `NoAvailableTierError` — `kind="unbound"` (deny
  work-class + no eligible metered-cloud tier, or a gated candidate with no bound backend) or
  `kind="exhausted"` (every bound candidate attempted and failed verify/relay). `kind="over_context"`
  is deliberately a **413**, not the exhaustion status.
- **C3 (no partial tokens):** the streaming path resolves `backend.generate()` eagerly inside the
  try, and the commit-window fully buffers + verifies before the first byte, so a pre-stream exhaustion
  becomes a real error status, never a 200 with an empty/truncated body.

**Re-verified live (in-process real HTTP server), 2026-07-04** — `pytest tests/router/ -k "exhaust or
no_tier or 424 or exhaustion or single_tier or keyless"` → **19 passed**. The load-bearing tests:

| test | proves |
|---|---|
| `test_serve_verify_fallback.py::…exhaustion…` (543 streaming / 579 non-streaming) | avw miss on a local-only single-tier config → **exhaustion_status (503)**, nothing streamed (C3) |
| `test_serve_verify_fallback.py:659 test_c3_keyless_exhaustion_configurable_status_424` | a config with `exhaustion_status=424` → the response uses **424, not 503** (real running server; `assert status == 424`) — proves an operator can match a different gateway's failover trigger |

### OpenClaw side — the live validation (2026-07-01, real gateway v0.6.0; recorded in ADR-0005)

A real OpenClaw agent turn drove the full path end-to-end for the first time:
`before_model_resolve` classified the turn as local-preferred and emitted
`{ providerOverride: "anvil", modelOverride: "<preset>" }`; anvil could not serve locally and returned
its keyless-handoff 503; **OpenClaw's native failover DID fire** (`fallbacks` was
`["openai/gpt-5.5","openai/gpt-5.4-mini"]`) — **confirming the first half of ADR-0001's mechanism: the
503 trips the "overloaded" category.**

### The caveat — the `providerOverride` failover loop (ADR-0005)

In that same run **both fallback attempts also 503'd**, because they resolved through the **`anvil`**
provider, not `openai`. Root cause (source-grounded): OpenClaw applies the `before_model_resolve`
`providerOverride` for the run's **entire attempt loop**, so the fallback walk's model strings
re-resolve against the pinned provider instead of each fallback entry's own provider.

- **The keyless 503 → native-failover handoff is unreliable once the plugin emits
  `providerOverride:"anvil"`**. Cloud-preferred presets now avoid the path by routing directly to a
  configured native provider/model. For local-preferred presets (quick-edit/review/chat/long-context
  — the majority of traffic) a local-unable condition can
  surface as a **failed turn** rather than a graceful cloud handoff.
- **No repo-side code fix** exists (it is OpenClaw's attempt-loop provider resolution, not anvil's or
  the plugin's). Mitigations (both already shipped, no silent behavior change):
  - **A — `ANVIL_CLOUD_CLASSES`:** move a flaky/exhausted preset into the cloud-preferred set; its turns
    never touch anvil, so nothing is inherited by the failover walk.
  - **B — anvil's own opt-in metered cloud tier (recommended durable fix):** enable
    `configs/example-with-cloud.toml` + add at-risk work-classes to `[router].metered_cloud`. anvil's own
    `fallback.py` escalates to the bound cloud tier **inside the same `provider="anvil"` response** —
    anvil never returns 503 for those classes, so OpenClaw's (un)reliable failover is never invoked.
    Requires the explicit billing opt-in ADR-0001 already gates.

## Recorded `exhaustion_status`

**`503`** (the default). It maps to OpenClaw 2026.6.x's "overloaded" transport-failover category, so no
override is needed for OpenClaw. Operators on a gateway that classifies a *different* status as its
failover trigger set `[router].exhaustion_status = <that status>` (proven configurable — see the 424
test). This is unchanged by this finding.

## One observation (by design, does not affect failover)

The `/v1/route` **decision/introspection** endpoint returns a **plain 503** on exhaustion
(`front_door.py:421`), not the configurable `exhaustion_status` the serve paths use. This is **by
design**: ADR-0001 §"POST /v1/route shape" specifies that endpoint's statuses as `200 (decision) / 400
(malformed) / 503 (no suitable tier)` — a standard "can't produce a decision" 503, deliberately distinct
from the serve path's gateway-failover-trigger status. OpenClaw's transport failover only ever calls the
**serve** endpoints (`/v1/messages`, `/v1/chat/completions`) — never `/v1/route` — so it does **not**
affect the failover T005 concerns. (The research pass flagged the two 503s as an "inconsistency"; per the
ADR-0001 spec they are two intentionally-distinct contracts, so no change is warranted.)

## Resolution

The ADR-0001 UNCONFIRMED is **RESOLVED**: anvil's exhaustion-`exhaustion_status` (default 503, C3-clean,
operator-configurable) **does** trip OpenClaw's transport failover; the handoff to the native
subscription is **unreliable once `providerOverride:"anvil"` is pinned** (ADR-0005); current
cloud-preferred presets avoid that path with an explicit native route, and mitigation B is the
durable fix for local-preferred presets. No router data-plane runtime change is
required by this validation.
