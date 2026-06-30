# Project: anvil-serving advise-and-defer

## Summary

anvil-serving adopts a **subscription-first cost posture**: the **default path holds no cloud API
key**. anvil is a **local-serve + routing brain**; the **harness owns cloud** on its existing
subscription. The cloud backend becomes **opt-in and off by default**, and when enabled it is
explicitly **metered** (a per-intent mapping plus a per-tier cost dimension). Quality-driven
fallback stays **router-internal** — research confirmed the gateway (OpenClaw 2026.6.6) **cannot** do
quality-based fallback (transport-class failover only; no response-swap hook) — so in the keyless
default an `allow-with-verify` miss **exhausts** the local tier chain and anvil returns an honest
**"no available tier" (503)** with the **commit-window guaranteeing no partial local tokens**; the
gateway's transport failover then re-runs that request on the **native subscription provider**. The
release also exposes the routing brain standalone as **`POST /v1/route`** (a decision-only endpoint —
novel; NotDiamond's `select_model()` is the only precedent). This is **additive and config-gated**:
the existing keyed, router-internal verify+fallback (shipped in v0.3.0) remains valid for operators
who opt into a keyed cloud tier. Design basis: **ADR-0001** (`docs/adr/0001-cloud-cost-and-subscription-auth.md`)
and the implementation plan (`docs/PLAN-advise-and-defer.md`).

**Cost model.** Bill = flat subscription (harness-owned cloud) + free local GPU + **$0 metered API by
default**. Metered API is reachable only when an operator explicitly adds a cloud tier AND maps a
specific intent/work-class to it. See ADR-0001 for why a subscription cannot be relayed (and why
driving `claude -p` as a backend is rejected).

**Release:** v0.4.0

## Goals

- **Default to $0 metered API:** ship a **local-only** default config; a cloud tier is opt-in, off by
  default, and never holds a credential in the default path.
- **Keyless quality-fallback** that leans on the gateway: a local miss exhausts → honest 503 →
  gateway transport failover → the harness's native subscription provider — with the **no-partial-
  local-tokens** guarantee (C3) preserved end to end.
- **Make metered cost explicit and granular:** a per-intent opt-in map and a per-tier cost dimension
  surfaced in the decision log/metrics; an **optional, off-by-default** price sync from a free source.
- **Expose the routing brain standalone** (`POST /v1/route`) so plugins and non-OpenClaw harnesses can
  integrate the decision without the serve path.
- **No teardown:** the v0.3.0 keyed router-internal fallback stays valid; the change is additive and
  config-gated; the existing suite stays green.
- **Documentation is product-ready:** every doc, diagram, and image reflects the advise-and-defer
  design and reads as a polished product — no stale cloud-relay/cost framing, accurate architecture
  visuals, consistent terminology.

## Non-Goals

- **Relaying a subscription as an API** (or driving `claude -p` as a transparent cloud backend) — not
  ToS-compliant and lossy for tool-calling coding turns; rejected in ADR-0001.
- **Removing the keyed cloud relay** — it remains an opt-in path for single-endpoint harnesses that
  cannot route cloud themselves (they accept metered $).
- **Tool-call passthrough** (issue #42) — tracked separately; not in this release.
- **A public decision-endpoint spec / SDK** — `POST /v1/route` ships as anvil's own contract; broader
  standardization is out of scope.

## Requirements

- R001: **Cloud tier is opt-in, OFF by default.** The default config ships local tiers only; anvil
  loads and runs with **no cloud credential** present. A cloud tier exists only when an operator adds
  one explicitly. `configs/example.toml` is local-only; a separate worked example shows the opt-in
  metered cloud tier with a prominent "metered $" callout.
- R002: **Per-intent metered mapping.** A configured cloud tier is a routing **candidate only for the
  intents/work-classes explicitly listed** in a `metered_cloud` map (empty/absent by default). The
  routing policy gates the cloud tier out of the candidate pool for any unmapped work-class. There is
  **no global "use cloud" switch**.
- R003: **Cost dimension.** Each tier may carry cost fields (`$/Mtok` input/output). When a request is
  routed to (or served by) a metered cloud tier, the decision log and metrics surface the **estimated
  cost** from cost × token counts; local tiers report `0`. Costs never block the hot path.
- R004: **Optional off-by-default cost-sync.** An explicitly-enabled toggle refreshes per-tier cost
  fields from the free, MIT-licensed **LiteLLM pricing JSON** via stdlib `urllib` (cached at
  `~/.cache/anvil-serving/prices.json` with a TTL), normalizing the provider model key. Disabled or on
  any fetch failure → fall back to static config. No runtime dependency, no key.
- R005: **Keyless quality-fallback handoff.** In the local-only default, an `allow-with-verify` miss
  exhausts the local tier chain (`route_with_fallback`) and the front door returns an honest **"no
  available tier" 503**; the **commit-window** guarantees **no partial local tokens** were streamed
  first (C3). The exhaustion status is a **documented contract** with the gateway (configurable
  `exhaustion_status`, default 503), not an internal detail — it is what the harness's transport
  failover keys on to re-run on the native provider.
- R006: **Live-validate the gateway failover trigger.** Confirm against **OpenClaw 2026.6.6** that
  anvil's exhaustion-503 maps to OpenClaw's transport-failover ("overloaded") category and re-runs the
  request on the native subscription provider; capture the real request/response as evidence. If 503
  does not trigger it, emit the status OpenClaw classifies as a transport-failover trigger. (Resolves
  the one ADR-0001 `UNCONFIRMED`.)
- R007: **Standalone decision endpoint** — `POST /v1/route` runs `intent.resolve` + `policy.route`
  **without serving** and returns the decision. Request = a `/v1/chat/completions`-shaped body plus an
  optional `signals` object (`work_class`, `token_estimate`, `urgency`); response =
  `{tier: local|cloud, model, provider, work_class, reason, confidence, session_id}`; status 200
  (decision, even if `cloud`), 400 (malformed), 503 (no suitable tier). Advertised via discovery.
- R008: **Plugin upfront routing.** The OpenClaw `before_model_resolve` adapter routes **`deny`-class →
  the native provider directly** (no anvil round-trip) and **`allow`/`allow-with-verify` → anvil**,
  classifying client-side via the shared `tier0_keywords.json` (with `POST /v1/route` available as the
  authoritative override). The plugin ensures the gateway's `agents.defaults.model.fallbacks` lists the
  native provider so the R005 handoff works. The router core stays OpenClaw-free.
- R009: **Contract + docs reshape.** Contract **C4 (verify-and-fallback)** is reframed into two modes —
  *keyless* (exhaustion-503 → gateway transport failover) and *opt-in keyed* (router-internal escalation
  → 200) — in `docs/QUALITY-GATED-ROUTER.md`. `README.md` states the **local-only, no-metered default**
  and documents the opt-in metered cloud tier with a prominent billing caveat + the per-intent mapping.
  `CLAUDE.md` notes the cloud tier is opt-in/off-by-default. Cross-link ADR-0001.
- R010: **Backward compatibility (additive, config-gated).** The v0.3.0 keyed router-internal
  verify+fallback path remains valid when an operator configures a keyed cloud tier; nothing is torn
  down. The existing router suite stays green; all new behavior is gated behind config.
- R011: **Credential hygiene preserved.** The default path holds **no cloud credential**. A metered
  cloud tier's key is per-tier env-referenced on the Backend seam (R012 of v0.3.0); the decision log
  and metrics never persist secrets or full prompts unless calibration opt-in is explicitly set.
- R012: **Documentation production-readiness (audit + sweep).** Every public-facing and contributor
  doc — `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/` (incl. `QUALITY-GATED-ROUTER.md`,
  `OPENCLAW-INTEGRATION-SPEC.md`, the findings), `docs/adr/`, and the plugin docs — is **reconciled to
  the advise-and-defer design**: no stale or contradictory statements about cloud relaying, cost, the
  default path, or the routing model; consistent terminology; no internal-only references; the
  cost/metered story is clear and accurate to shipped behavior. A doc-audit punch-list drives the fixes
  so the repo reads as a polished product. Public docs are **audience-directed** — addressed to users
  and contributors, not to a single person — and read like an open-source project; second-person/
  personal framing and session-specific asides are rewritten or removed.
- R013: **Architecture diagrams & visual assets.** Architecture and request-flow **diagrams (mermaid)**
  reflect the advise-and-defer topology — local-serve + routing brain, the keyless exhaustion→gateway-
  failover handoff, the opt-in metered cloud, and the `POST /v1/route` decision surface. The **release
  brand images / diagram-images are refreshed (Nano Banana)** to match the new positioning; all visuals
  are accurate, consistent, and production-quality (README hero + docs visuals).
- R014: **Public/internal doc separation + auxiliary record-keeping repo (preserved, never lost).**
  Internal-model material — **product-design discussions and planning context** (`DIRECTION.md`,
  `BLUEPRINT.md`, `DESIGN-DISCUSSION-*`, `*-PLANNING-CONTEXT.md`), session retros, and worklog-style
  findings — is **relocated to a separate private auxiliary repo** for record-keeping (**never
  deleted**). That repo is **registered where future sessions will find it** (a pointer in `CLAUDE.md`
  + auto-memory) so the references stay available across sessions. The public repo keeps only
  audience-directed docs; where a relocated discussion holds substance a visitor would value, its
  essence is **distilled into a clean, audience-facing doc** (a "Design"/"How it works" page) rather
  than exposing the raw internal reasoning.
- R015: **Published documentation site ("Read the Docs").** A browsable docs site (e.g. MkDocs Material
  published to Read the Docs or GitHub Pages) is generated from the public docs — navigation, quickstart,
  the architecture + diagrams, the cost/metered model, and the ADRs — so the product has a real docs
  home, not just a README. The build is reproducible and wired into CI.

## Features

### F001: Opt-in metered cloud (off by default) — per-intent mapping + cost dimension
Groups R001, R002, R003, R011. The cloud tier is opt-in; metered access is per-intent; cost is a
first-class, surfaced dimension; the default path is keyless and credential-free.

### F002: Keyless quality-fallback handoff
Groups R005, R006, R010. Local miss → exhaustion-503 (no partial tokens) → gateway transport failover →
native subscription provider; validated live; the keyed path stays valid.

### F003: Optional off-by-default cost-sync
Groups R004. Refresh per-tier costs from the free LiteLLM pricing JSON when explicitly enabled; static
config otherwise.

### F004: Standalone routing decision endpoint
Groups R007. `POST /v1/route` exposes the routing brain without the serve path.

### F005: Plugin upfront routing
Groups R008. The OpenClaw adapter splits deny→native vs allow/avw→anvil client-side; router core stays
OpenClaw-free.

### F006: Contract & docs reshape
Groups R009. C4 two-mode reframe; README/CLAUDE.md updated for the local-only default + metered caveat.

### F007: Documentation & visuals — production-ready
Groups R012, R013, R014, R015. A holistic doc-and-visuals sweep so the whole repo — prose, diagrams,
images, the public/internal split, and a published docs site — is accurate to the advise-and-defer
design and reads as a shipped open-source product.

## Risks

- **Gateway failover trigger unconfirmed (R006).** If OpenClaw does not fail over on anvil's
  exhaustion-503, the keyless handoff needs a different status; mitigated by a configurable
  `exhaustion_status` and the live-validation task gating M0.
- **Reduced local utilization.** Routing borderline classes that miss verify to the subscription (vs a
  keyed cloud relay) leans more on subscription rate limits; acceptable since the bill stays flat.
- **Single-endpoint harnesses (Codex, raw Claude Code)** cannot do per-request fallback; they need the
  gateway or the opt-in metered relay — documented, not blocked.

## Open Questions

- Does the OpenClaw plugin embed the classifier or call `POST /v1/route` as the authoritative source
  (decide after F004 lands)?
- Does the optional cost-sync grow beyond a single static-JSON fetch (would warrant a follow-up ADR)?

## Milestones / MVP cut

M0 makes the shipped default keyless and proves the handoff (these gate the public flip); M1–M2 are
incremental and can land after the v0.3.0 public launch.

- **M0 — keyless default + handoff (launch-relevant):** T001, T002, T003, T004, T005.
- **M1 — cost-sync + decision endpoint:** T006, T007.
- **M2 — plugin + docs reshape:** T008, T009.
- **M3 — documentation & visuals (production-ready):** T010, T011, T012, T013, T014, T015. Runs last so
  docs, diagrams, the public/internal split, and the docs site reflect the shipped behavior from M0–M2.

## Tasks

### T001: Make the cloud tier opt-in, OFF by default (local-only default config)

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/router/config.py, configs/example.toml, configs/example-with-cloud.toml

Make a cloud tier something an operator adds explicitly. Ship `configs/example.toml` as **local-only**
(no cloud tier, no credential needed to load or run). Add `configs/example-with-cloud.toml` showing the
opt-in cloud tier with a loud "this incurs metered API billing" comment. The router must load and serve
with zero cloud tiers configured. (M0)

**Acceptance criteria:**

- Loading the default `configs/example.toml` yields **only local tiers**; no cloud credential is required to load or run.
- `build_server` starts and serves with zero cloud tiers configured (local-only).
- `configs/example-with-cloud.toml` documents the opt-in metered cloud tier.

**Verification:**

- `pytest tests/router/test_config.py tests/router/test_serve_cli.py`
- `python -c "from anvil_serving.router.config import load; print([t.privacy for t in load('configs/example.toml').tiers])"`

### T002: Per-intent metered mapping + policy gate

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/router/config.py, anvil_serving/router/policy.py
**Dependencies:** T001

Add a `metered_cloud` map (intents/work-classes permitted to use a metered cloud tier; empty/absent by
default) to the config, and gate the routing policy so a cloud tier is dropped from the candidate pool
for any unmapped work-class. No global cloud switch. (M0)

**Acceptance criteria:**

- With a cloud tier configured but `metered_cloud` empty, the cloud tier is **never** a routing candidate.
- A work-class listed in `metered_cloud` makes the cloud tier a candidate for that class only.

**Verification:**

- `pytest tests/router/test_policy.py tests/router/test_config.py`

### T003: Cost dimension — per-tier cost fields surfaced in decision log + metrics

**Feature:** F001
**Priority:** medium
**Likely files:** anvil_serving/router/config.py, anvil_serving/router/decision_log.py, anvil_serving/router/metrics.py
**Dependencies:** T001

Add optional per-tier cost fields (`$/Mtok` input/output) and surface estimated cost (cost × token
counts) in the decision record + a `cost_usd` metric when a metered cloud tier is used; local tiers
report `0`. Never blocks the hot path. (M0)

**Acceptance criteria:**

- A metered-cloud route records an estimated `cost_usd` in the decision log; a local route records `0`.
- Cost fields are optional in config (absent → unknown/None, no crash).

**Verification:**

- `pytest tests/router/test_transparency.py tests/router/test_metrics.py`

### T004: Keyless quality-fallback handoff — exhaustion status contract + C3

**Feature:** F002
**Priority:** high
**Likely files:** anvil_serving/router/serve.py, anvil_serving/router/fallback.py, anvil_serving/router/front_door.py
**Dependencies:** T001

In the local-only default, an `allow-with-verify` miss exhausts the tier chain and the front door
returns an honest **"no available tier" 503** with **no partial local tokens** (commit-window). Make
the exhaustion status a documented, configurable contract (`exhaustion_status`, default 503). (M0)

**Acceptance criteria:**

- Local-only config + an `allow-with-verify` request whose local output fails verify → **503**, and the broken local token appears **nowhere** in the response body (C3).
- The exhaustion status is configurable; default 503.

**Verification:**

- `pytest tests/router/test_serve_verify_fallback.py`

### T005: Live-validate the OpenClaw failover trigger (resolve the ADR-0001 UNCONFIRMED)

**Feature:** F002
**Priority:** high
**Likely files:** docs/findings/
**Dependencies:** T004

Against a live **OpenClaw 2026.6.6** gateway, confirm anvil's exhaustion-503 triggers OpenClaw's
transport failover and re-runs the request on the native subscription provider; capture the real
request + the failover evidence. If 503 does not trigger it, determine the status OpenClaw classifies
as a transport-failover trigger and set `exhaustion_status` accordingly. Write up the result. (M0)

**Acceptance criteria:**

- A captured live run shows: anvil local miss → 503 → OpenClaw fails over → native provider served the request.
- The confirmed `exhaustion_status` is recorded; a finding doc under `docs/findings/` documents the evidence.

**Verification:**

- Manual live run on the gateway; evidence captured in `docs/findings/<date>-openclaw-keyless-failover.md`.

### T006: Optional off-by-default cost-sync (free LiteLLM pricing JSON)

**Feature:** F003
**Priority:** medium
**Likely files:** anvil_serving/router/prices.py, anvil_serving/router/config.py, anvil_serving/cli.py
**Dependencies:** T003

Add an explicitly-enabled (off by default) cost-sync that GETs the MIT LiteLLM pricing JSON via stdlib
`urllib`, caches it (`~/.cache/anvil-serving/prices.json`, TTL), normalizes the provider model key, and
fills unset per-tier cost fields. Disabled or fetch-failure → static config. (M1)

**Acceptance criteria:**

- Sync is **off by default**; enabling it maps a Claude/GPT model id → cost from a fixture pricing JSON.
- A fetch failure falls back to static config without crashing; no runtime dependency, no key.

**Verification:**

- `pytest tests/router/test_prices.py`

### T007: `POST /v1/route` decision endpoint

**Feature:** F004
**Priority:** medium
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/router/serve.py, anvil_serving/router/discovery.py
**Dependencies:** T002

Add `POST /v1/route` that runs `intent.resolve` + `policy.route` **without serving** and returns
`{tier, model, provider, work_class, reason, confidence, session_id}` (status 200/400/503). Accept a
chat-shaped body + optional `signals`. Advertise it in discovery. (M1)

**Acceptance criteria:**

- `POST /v1/route` returns a well-formed decision for allow / allow-with-verify / deny / metered-mapped intents and **never** triggers a backend call.
- Malformed body → 400; no suitable tier → 503.

**Verification:**

- `pytest tests/router/test_front_door.py`

### T008: Plugin upfront routing (OpenClaw `before_model_resolve`)

**Feature:** F005
**Priority:** medium
**Likely files:** plugins/openclaw-anvil-intent-router/index.ts, plugins/openclaw-anvil-intent-router/classify.mjs
**Dependencies:** T007

Extend the adapter so `before_model_resolve` routes **`deny`-class → the native provider directly** and
**`allow`/`allow-with-verify` → anvil** (client-side classify via the shared vocab; `POST /v1/route`
available as the authoritative override). Ensure the gateway fallback list includes the native provider
so the keyless handoff works. Router core stays OpenClaw-free. (M2)

**Acceptance criteria:**

- A `deny`-class request is routed to the native provider without an anvil round-trip; `allow`/`allow-with-verify` go to anvil.
- The shared keyword-parity guard still passes (no classifier drift).

**Verification:**

- Plugin unit tests; `pytest tests/router/test_keyword_parity.py`

### T009: Contract + docs reshape (C4 two modes; README/CLAUDE.md)

**Feature:** F006
**Priority:** medium
**Likely files:** docs/QUALITY-GATED-ROUTER.md, README.md, CLAUDE.md
**Dependencies:** T004, T007

Reframe contract **C4** into *keyless* (exhaustion-503 → gateway failover) and *opt-in keyed*
(router-internal → 200) modes. State the **local-only, no-metered default** in `README.md` and document
the opt-in metered cloud tier with a prominent billing caveat + the per-intent mapping. Note the opt-in
posture in `CLAUDE.md`. Cross-link ADR-0001. (M2)

**Acceptance criteria:**

- `docs/QUALITY-GATED-ROUTER.md` C4 section describes both modes accurately.
- `README.md` states the default is local-only/no-metered and documents the opt-in metered cloud with a billing callout.

**Verification:**

- Doc review against ADR-0001 + `docs/PLAN-advise-and-defer.md`.

### T010: Documentation audit & prose sweep (reconcile all docs to advise-and-defer)

**Feature:** F007
**Priority:** high
**Likely files:** README.md, CLAUDE.md, AGENTS.md, docs/QUALITY-GATED-ROUTER.md, docs/OPENCLAW-INTEGRATION-SPEC.md, docs/DIRECTION.md, docs/BLUEPRINT.md
**Dependencies:** T004, T007, T009

Inventory **every** public-facing and contributor doc and reconcile it to the advise-and-defer design.
Produce a doc-audit punch-list (file → stale/contradictory statement → fix), then fix the prose: remove
any stale cloud-relay/cost framing, align terminology (local-serve + routing brain, opt-in metered
cloud, keyless handoff), drop internal-only references, and make the cost/metered story accurate to
shipped behavior. Rewrite any direct-address/personal voice ("you", session asides) into
audience-directed, open-source-project prose. The repo should read as a polished product. (M3)

**Acceptance criteria:**

- A punch-list enumerates every doc touched and the stale statements found.
- No doc contradicts the advise-and-defer design (no "anvil relays cloud by default", no metered-by-default framing); terminology is consistent across docs.

**Verification:**

- `grep -rniE "relay.*cloud|cloud.*by default|api key" docs/ README.md CLAUDE.md` returns no stale/contradictory hits (or each is justified).
- Doc review against ADR-0001 + `docs/PLAN-advise-and-defer.md`.

### T011: Architecture & request-flow diagrams (mermaid)

**Feature:** F007
**Priority:** medium
**Likely files:** docs/QUALITY-GATED-ROUTER.md, README.md, docs/diagrams/
**Dependencies:** T004, T007

Create/update **mermaid** diagrams that reflect the advise-and-defer topology: the local-serve +
routing-brain split, the keyless `allow-with-verify` miss → exhaustion-503 → gateway transport
failover → native subscription provider, the opt-in metered cloud path, and the `POST /v1/route`
decision surface. Embed them in the relevant docs. (M3)

**Acceptance criteria:**

- A routing-topology diagram and a request-flow (keyless handoff) diagram exist as mermaid and render correctly.
- The diagrams match the shipped behavior (local default, keyless handoff, opt-in metered cloud).

**Verification:**

- mermaid renders without error; diagram content reviewed against ADR-0001.

### T012: Brand & visual assets refresh (Nano Banana)

**Feature:** F007
**Priority:** medium
**Likely files:** docs/brand/, README.md, assets/
**Dependencies:** T011

Refresh the release brand images and any diagram-as-image assets (the #58 brand images) to match the
advise-and-defer positioning using **Nano Banana**; ensure the README hero and docs visuals are
accurate (local-first, subscription-friendly, $0 metered by default) and production-quality. Keep
mermaid for technical diagrams; use Nano Banana for brand/hero/positioning visuals. (M3)

**Acceptance criteria:**

- The release brand/hero visuals reflect the new positioning (no stale cloud-relay/cost imagery).
- Images are consistent in style and referenced correctly in README/docs (no broken links).

**Verification:**

- Visual review; `grep -rnoE "!\[[^]]*\]\([^)]*\)" README.md docs/` links resolve to existing assets.

### T013: Production-readiness review pass (the public surface reads as a product)

**Feature:** F007
**Priority:** medium
**Likely files:** README.md, docs/, CONTRIBUTING.md
**Dependencies:** T010, T011, T012

A final holistic pass so the public surface reads as a coherent, shippable product: consistent
terminology and tone, accurate quickstart, the cost/metered story unambiguous, no internal-only or
TODO/scratch references, working links and visuals, and ADR-0001 + the plan cross-linked. (M3)

**Acceptance criteria:**

- The README + docs present a consistent, accurate product story end-to-end (quickstart works against the local-only default; the opt-in metered cloud is clearly caveated).
- No leftover internal-only references, dead links, or TODO/scratch notes in public docs.

**Verification:**

- Product-readiness checklist reviewed; `grep -rniE "TODO|FIXME|XXX|INTERNAL ONLY" README.md docs/*.md` returns no public-facing leftovers (or each is justified).

### T014: Public/internal doc split + auxiliary record-keeping repo (preserved + session-discoverable)

**Feature:** F007
**Priority:** high
**Likely files:** docs/DIRECTION.md, docs/BLUEPRINT.md, docs/DESIGN-DISCUSSION-2026-06-27.md, docs/FAKOLI-DARK-V2-PLANNING-CONTEXT.md, docs/findings/, CLAUDE.md
**Dependencies:** T010

Classify every doc **public** (audience-facing, product-quality) vs **internal** (product-design
discussions, planning context, session retros, worklog-style findings, personal-model material).
Create a **private auxiliary repo** (e.g. `fakoli/anvil-serving-notes`) and **relocate** the internal
docs there for record-keeping — **never delete them**. **Register the auxiliary repo where future
sessions will find it**: a pointer in `CLAUDE.md` and an auto-memory entry, so the references stay
available across sessions. Where a relocated discussion holds substance a visitor would value, distill
its essence into a clean, audience-facing public doc rather than exposing the raw internal reasoning. (M3)

**Acceptance criteria:**

- No internal-model / personal-worklog / raw design-discussion docs remain in the public tree; the public `docs/` is audience-facing only.
- The auxiliary private repo holds the relocated docs (nothing lost) and is registered in `CLAUDE.md` + auto-memory so future sessions can reach it.
- Any publicly-valuable substance from relocated docs is distilled into a clean public doc.

**Verification:**

- The auxiliary repo exists and contains the relocated docs; `CLAUDE.md` + memory reference it.
- A review confirms the public `docs/` contains no internal-discussion/worklog/personal-model files.

### T015: Published documentation site ("Read the Docs")

**Feature:** F007
**Priority:** medium
**Likely files:** mkdocs.yml, docs/, .github/workflows/docs.yml
**Dependencies:** T010, T011, T013

Stand up a browsable documentation site from the public docs (e.g. **MkDocs Material** published to
Read the Docs or GitHub Pages): navigation, quickstart, the architecture + mermaid diagrams, the
cost/metered model, and the ADRs. The build is reproducible and wired into CI so the site cannot drift.
The product gets a real docs home, not just a README. (M3)

**Acceptance criteria:**

- `mkdocs build --strict` (or the chosen generator) builds the site cleanly from the public docs, including quickstart, architecture/diagrams, cost model, and ADRs.
- A CI job builds (and ideally publishes) the docs site; the README links to it.

**Verification:**

- `mkdocs build --strict` succeeds locally; the CI docs job is green.
