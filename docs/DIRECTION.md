# DIRECTION — what anvil-serving is building, and why

> **North-star + evidence dossier.** rev 2026-06-29. This is the single document that states the
> project's thesis, the product we are building, and the **cited decision chain** that took us from
> "a local serving tier for Anvil" to "a harness-facing quality-gated router." Every load-bearing
> claim points at a source (`path` or `path:line`). The numbers here are pulled from the findings —
> none are invented.
>
> **Design / spec / plan it summarizes:**
> - Design: [`QUALITY-GATED-ROUTER.md`](QUALITY-GATED-ROUTER.md)
> - OpenClaw integration: [`OPENCLAW-INTEGRATION-SPEC.md`](OPENCLAW-INTEGRATION-SPEC.md)
> - PRD: [`prd/anvil-serving-harness-router.prd.md`](prd/anvil-serving-harness-router.prd.md) (tracked in Anvil as PRD `harness-router` → v0.3.0, 18 tasks)
> - Evidence: the four findings under [`findings/`](findings/) dated 2026-06-28/29
> - Bake-off context: [`../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md`](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md)

---

## 1. Bottom line / thesis

> anvil-serving is the **workload-aware, correctness-gated local-model router** for coding harnesses.
> **Local where it's been proven, cloud where it hasn't — verified, with automatic fallback.**

A coding harness (Claude Code, Codex, Aider, Cline, Continue; **OpenClaw** as the near-first-class
beachhead) points at **one** anvil-serving endpoint. Per request the router:

1. **resolves an INTENT** — a named-preset use-case carried in the `model` field
   ("model-name-as-intent"), or inferred when none is declared;
2. **routes to a TIER** — fast-local / heavy-local / cloud — using a **measured per-(model,
   work-class) quality profile** ("filter by hard constraints, then rank by measured quality");
3. **cheaply VERIFIES** the output (structural checks, no inline LLM grading on the hot path); and
4. **FALLS BACK** up the tier chain to cloud on failure.

The harness sees one reliable endpoint and **never eats a silent local-quality failure mid-run**.

The defensible asset is **not** the proxy (transport is commodity — LiteLLM, claude-code-router,
Ollama, OpenRouter all exist). It is the **quality profile** — per model × work-class, measured on
the user's own workload — plus the **verify-and-fallback loop**
([`QUALITY-GATED-ROUTER.md:26-40`](QUALITY-GATED-ROUTER.md)). Competitors copy transport in a
weekend; they cannot copy "we measured that `gpt-oss-20b` is safe for bounded edits but unsafe for
dependency planning on *your* repos."

The core stays **protocol-standard** (Anthropic Messages + OpenAI Chat Completions) with **zero
OpenClaw coupling**; OpenClaw is a thin, swappable adapter — **focus, not couple**.

---

## 2. What we're building

### 2.1 The router — one front door, two dialects, three tiers

```
          ┌──────────────────── anvil-serving router ───────────────────┐
harness → │ front door → resolve intent → route → [verify] → return     │ → harness
(CC/Codex)│  (Anthropic   (preset in        (filter by    │  on fail ↘          │
          │   +OpenAI      model field, else  constraints, │  fall back to       │
          │   dialects)    infer work-class)  rank by       │  next tier / cloud  │
          │                                   quality profile)                   │
          └───────────────────────────────┬──────────────┴────────────────────┘
                                           ▼
                fast-local :30001   heavy-local :30000   cloud (Anthropic/OpenAI)
                  (multiplexer-managed)                    (user's existing key/sub)
```
(from [`QUALITY-GATED-ROUTER.md:68-79`](QUALITY-GATED-ROUTER.md))

- **Control plane (slow, offline):** `profile` → shadow-eval → the **routing table** (the quality
  profile), refreshed on demand and continuously calibrated from sampled production traffic.
- **Data plane (fast, per-request):** resolve intent → route → optional inline verify → fallback.
  Must add negligible latency and **must stream**.

On `fakoli-dark` the two local tiers are a **single-resident swap pair on one box**: heavy `:30000`
= `qwen3-coder-30b` on SGLang (RTX PRO 6000, 96GB); fast `:30001` = `gpt-oss-20b` on vLLM
(RTX 5090, 32GB), managed by the existing `multiplexer`. Cloud uses the user's own existing
key/subscription.

### 2.2 The intent → tier → verify → fallback loop

- **Intent addressing.** Callers declare a **use-case**, not a model: a closed enum of presets —
  `planning`, `quick-edit`, `review`, `chat`, `long-context` — carried in the `model` field. The
  router owns `intent → (model, tier, params)`
  ([`QUALITY-GATED-ROUTER.md:41-65`](QUALITY-GATED-ROUTER.md)). Each preset resolves internally to
  **hard constraints** (context length, privacy=local-only, tool/structured-output support, cost
  ceiling) that *filter* the candidate pool, plus a quality intent that *ranks* the survivors via
  the profile. **Filter, then rank.** A `model:` pin escape hatch stays available for repro/debug.
- **The quality profile (the moat).** A table keyed `(model, work-class)` →
  `{quality_score, sample_n, last_measured, decision}` where
  `decision ∈ {allow, allow-with-verify, deny}`
  ([`QUALITY-GATED-ROUTER.md:88-101`](QUALITY-GATED-ROUTER.md)). MVP seeds it **hand-authored**;
  post-MVP it is bootstrapped from the shadow-eval harness, right-sized from real usage via
  `profile`, and continuously calibrated off the hot path. It is keyed on a **serve fingerprint**
  (model + quant + engine + serve flags) so model/quant/serve swaps invalidate the right rows.
- **Verify (the honest hard part).** Inline LLM-grading every response would defeat the purpose, so
  most quality control is **routing done ahead of time**; verification is a cheap, tiered safety net
  ([`QUALITY-GATED-ROUTER.md:132-151`](QUALITY-GATED-ROUTER.md)): **(1) prevent** — never send a
  `deny` class to local; **(2) cheap structural verify inline** — empty/truncated content, tool-call
  JSON that doesn't validate, code that won't parse, a diff that won't apply (all real failure modes
  from the eval, see §3.2); **(3) confidence signals** where available; **(4) async LLM-judge** off
  the hot path, feeding the profile, never a blocking gate.
- **Fallback.** On verify-fail / error / timeout / low-confidence → retry next tier up
  (fast→heavy→cloud). Guardrails: cap retries, circuit-break repeat offenders, enforce a
  per-session/window **cost budget**, keep fallback idempotent for the harness (especially
  mid-stream), and **log every fallback** — a fallback is itself a profile signal that the class may
  need downgrading to `deny`.

### 2.3 The tier ladder (graceful degradation)

Intent resolution degrades to the **highest tier the originating harness can reach**. The Tier-0
classifier is **not optional** — it is the universal floor, because most requests arrive on a single
session model string with no declared intent
([`QUALITY-GATED-ROUTER.md:104-120`](QUALITY-GATED-ROUTER.md),
[`findings/2026-06-29-harness-intent-routing.md:59-70`](findings/2026-06-29-harness-intent-routing.md)):

| Tier | Mechanism | What it unlocks | Available on |
|---|---|---|---|
| **0 — Infer** | classify work-class from raw payload (token count, `thinking` flag, tool types, image content, system-prompt fingerprint) | per-request intent **with no caller cooperation** — the default operating mode | every harness that reaches the endpoint |
| **1 — Named presets in `model`** | caller/config sets a preset token; router maps preset → tier | caller-declared **coarse** (session-slot) intent | Claude Code, Codex, Aider, Cline, Continue — **not** Cursor/Amp/Devin |
| **2 — extra_body / header dimensions** | optional structured hints (budget, latency, verifier policy) | multi-axis intent beyond the flat string — config-level, not per-request | Codex, Continue; Aider (config). Not Claude Code/Cursor |
| **3 — Native intent field** | a first-class per-request intent field | true per-request multi-axis intent | **none today** — needs a standard/harness change |

`claude-code-router` is the production existence proof for Tier 0+1
([`findings/2026-06-29-harness-intent-routing.md:67-70`](findings/2026-06-29-harness-intent-routing.md)).

### 2.4 OpenClaw beachhead — focus, not couple

OpenClaw is the **one in-scope harness that crosses into per-request routing**: its
`before_model_resolve` hook can return `modelOverride`/`providerOverride`, so a small client plugin
classifies each user message and emits an anvil preset id on the wire — escaping the session-coarse
limit that binds the closed harnesses
([`QUALITY-GATED-ROUTER.md:181-199`](QUALITY-GATED-ROUTER.md),
[`findings/2026-06-29-openclaw-hermes-customization.md:34-49`](findings/2026-06-29-openclaw-hermes-customization.md)).
That is why it gets the integration depth.

**This is focus, not coupling.** The core router stays protocol-standard with **zero OpenClaw
import**; the OpenClaw piece is a **~50-line, one-hook, swappable adapter plugin**
([`OPENCLAW-INTEGRATION-SPEC.md:42-47`](OPENCLAW-INTEGRATION-SPEC.md)). If the hook churns, only the
adapter changes; if OpenClaw stalls, the router is unaffected and another hook-capable harness takes
the beachhead. Same lesson as not coupling to Anvil: integrate at a standard seam, focus effort at
one client. Verdict on record: **GO, with caveats; maturity risk MEDIUM** (API churn, not
abandonment) ([`OPENCLAW-INTEGRATION-SPEC.md:19`](OPENCLAW-INTEGRATION-SPEC.md)).

---

### 2.5 Where it runs (environment)

Two boxes. **Fakoli Mini** is the gateway — **OpenClaw is already installed there** (it bridges chat
surfaces and is the beachhead client). **fakoli-dark** is the GPU serving box (heavy `:30000`
qwen3-coder-30b on SGLang + fast `:30001` gpt-oss-20b on vLLM). The anvil-serving **router** sits
between them: gateway → router → local serves / cloud. Because OpenClaw is already live on Fakoli
Mini, the validate-first gaps (hook cadence, wire-`model` form) are confirmed against a **real
install**, not a fresh stand-up.

## 3. Why this direction — the decision chain (cited evidence)

We did not start here. The original plan (Anvil bake-off task **T013**) was "wire both serving tiers
into Anvil's config so the fleet uses the local servers." Four pieces of research, in order,
**falsified that premise and rebuilt the product around where the evidence actually pointed.** Each
note below records what it asked, what it found (with the real numbers), and what it changed.

### 3.1 Integration audit — Anvil is not an LLM gateway

**Source:** [`findings/2026-06-28-anvil-integration-audit.md`](findings/2026-06-28-anvil-integration-audit.md)
(7-agent audit of the `anvil` repo, ~509k tokens, adversarial premise-verifier; verdict
**premise-partial**).

**What it asked.** Should anvil-serving connect to Anvil, and what is the correct `.anvil/config.yaml`
shape for routing-by-role across the two local tiers?

**What it found.** The T013 premise is **half-correct**. True parts: Anvil reads a per-project
`.anvil/config.yaml` ([`config.py:316`]), has a real `llm_provider: custom` key
([`config.py:77`]), and a wired `CustomEndpointProvider` that builds an OpenAI client against any
`/v1` endpoint ([`planning/llm.py:955-973`]) — so pointing **one** endpoint at the heavy local
server is valid and supported (cited at
[`audit:24-26`](findings/2026-06-28-anvil-integration-audit.md)). But the parts that **refute** the
plan as written ([`audit:28-34`](findings/2026-06-28-anvil-integration-audit.md)):

- **No two-tier endpoint routing.** `Config` has exactly **one** `custom_base_url` field per process
  ([`config.py:112`]). You cannot express "`:30000` heavy, `:30001` fast." `llm_tier`
  (opus/sonnet/haiku) maps to **model-name strings** sent to that single base_url, never a second
  endpoint ([`planning/llm.py:221-225`]).
- **Anvil ships no router.** Dynamic difficulty-based escalation is explicitly deferred behind a
  future `llm_router:` key ([`docs/model-strategy.md:49,55`]).
- **The custom endpoint backs ONLY Anvil's optional planning augmentation** (`plan/score/expand
  --use-llm` + the MCP planning tools) — the *only* place Anvil talks to an LLM
  ([`planning/llm.py:3-6`]). It does **not** route the coding agent's traffic; that is owned by the
  surrounding harness ([`docs/model-strategy.md:82`]).

**What it changed.** The integration point is the **runtime / harness, not Anvil**
([`audit:93-104`](findings/2026-06-28-anvil-integration-audit.md)). The bulk of the tokens — the
coding-agent fleet's traffic — never passes through Anvil. So "wire both tiers into Anvil" is the
wrong place to build; the heavy/fast role routing must live in the agent runtime / proxy layer. This
is the first pivot: **stop building into Anvil's config; build a router at the harness seam.** (In
the PRD this retires bake-off T013 — see §5.)

### 3.2 Planning-capability eval — local quality is work-class-dependent and *measurable*

**Source:** [`findings/2026-06-28-planning-capability-eval.md`](findings/2026-06-28-planning-capability-eval.md)
plus raw data under [`findings/eval-data/2026-06-28-planning-capability/`](findings/eval-data/2026-06-28-planning-capability/).

**What it asked.** Is the local serving tier smart enough to do Anvil's planning augmentation? Run
Anvil's *real* PRD→tasks prompt against the local tiers vs frontier and grade it.

**What it found.** On Anvil's verbatim planning prompt (one-shot, temp 0, `max_tokens=8192`,
2 real PRDs), local lands at **~55–65% of frontier quality**. Blind-judge totals (4 independent Opus
judges, anonymized candidates, averaged over 2 PRDs × 2 judges,
[`eval:62-71`](findings/2026-06-28-planning-capability-eval.md)):

| Model | Dependency correctness | **Total / 25** | % of frontier |
|---|---|---|---|
| **FRONTIER (Opus 4.8)** | **5.0** | **24.75** | 100% |
| **FAST (gpt-oss-20b)** | **2.0** | **16.0** | **64.6%** |
| **HEAVY (qwen3-coder-30b)** | **2.25** | **13.25** | **53.5%** |

Two findings are load-bearing for the product:

1. **The gap is squarely in DEPENDENCY / ORDERING reasoning.** Both local models score ~2/5 on
   dependency correctness while frontier scores a perfect 5/5 — "the single clearest quantitative
   signal in the eval" ([`eval:64-72`](findings/2026-06-28-planning-capability-eval.md)). The thing
   frontier did that *neither* local model managed was honoring a subtle non-local ordering
   constraint stated in the requirements (PRD-B's R007 "pin the moat regression tests *before*
   `--prd` narrowing") ([`eval:160-168`](findings/2026-06-28-planning-capability-eval.md)).
2. **Structural validity is NOT the differentiator.** Every output parses cleanly under Anvil's own
   rules (≥92%), no cycles, no dangling edges, full field completeness
   ([`eval:113-129`](findings/2026-06-28-planning-capability-eval.md)). Modern models reliably follow
   the strict format. **The capability gap hides behind valid-looking output** — which is exactly why
   a dumb proxy is dangerous: it ships structurally-fine, semantically-wrong work into a long agent
   run.

Failure modes are **complementary and PRD-dependent**, not a uniform deficit
([`eval:73-91`](findings/2026-06-28-planning-capability-eval.md)): HEAVY tanks on the breadth-y PRD-A
(10/25) by under-decomposing into 9 mega-tasks; FAST tanks on the infra-heavy PRD-B (15/25) by
silently dropping two whole phases (F008, F009). Latency was a **non-issue** for planning (~190 tok/s,
8–18 s end-to-end) — the feared 35 s TTFT was a 125k-context-prefill artifact, irrelevant to short
planning prompts ([`eval:104-107`](findings/2026-06-28-planning-capability-eval.md)). So the question
is purely **quality**, not speed.

**What it changed.** Two things. First, **don't route Anvil's own planning to local** — it's a
quality-sensitive, low-volume task Anvil already gets **free** from the subscription; trading
24.75→~14 quality to save \$0 is the worst trade in the system
([`eval:217-225`](findings/2026-06-28-planning-capability-eval.md)). Planning stays cloud (§4).
Second, and bigger: because the gap is **per-work-class and measurable**, routing can be
**evidence-based**. This is the conceptual seed of the whole product —
**route by measured work-class; don't send local what it can't do**
([`QUALITY-GATED-ROUTER.md:30-40`](QUALITY-GATED-ROUTER.md)). The shadow-eval harness that produced
these numbers is the bootstrap for the quality profile (§2.2). Caveats on record: n=2 PRDs, both
Anvil's own infra-dense PRDs; single decoding at temp 0 (best-of-n + a validator-retry loop would
close part of the gap) ([`eval:172-189`](findings/2026-06-28-planning-capability-eval.md)).

### 3.3 Harness research — the `model` string is the only routing channel

**Source:** [`findings/2026-06-29-harness-intent-routing.md`](findings/2026-06-29-harness-intent-routing.md)
(11-agent web-research workflow, ~537k tokens, adversarial verification; verdict
**confirm-with-refinement**).

**What it asked.** Can coding harnesses carry routing intent to anvil-serving, and is "named presets
in the model field" the right API surface?

**What it found.** Across **every** harness that can be repointed at a custom endpoint, the `model`
string is the *only* operator-controllable routing channel that is (a) always present (required in
both wire schemas), (b) forwarded verbatim, and (c) free-form — neither the OpenAI nor the Anthropic
schema validates it against a closed enum; only the *genuine upstream* rejects unknown names. A
router behind the base_url is free to reinterpret an arbitrary `model` string
([`harness:34-44`](findings/2026-06-29-harness-intent-routing.md)). This is exactly how shipping
gateways behave (OpenRouter slugs `:nitro`/`:floor`/`:exacto`, LiteLLM aliases, Cloudflare AI Gateway
`dynamic/<route>`). So "named presets in the model field" is the **correct compatibility floor**.

The **refinement** (why it isn't the whole answer,
[`harness:46-57`](findings/2026-06-29-harness-intent-routing.md)):

1. **Granularity is SESSION-coarse, not per-request.** An unmodified harness pins the model across a
   small fixed set of slots per session (Claude Code ~3–4: main / haiku-background / subagent /
   advisor; Codex 1+subagent). It does **not** vary the model by work-class *within* the main loop.
   Finer per-request intent must be **inferred**.
2. **Presets-only must be paired with a classifier (Tier 0) as the default path**, because most
   requests arrive on a single session model string with no declared intent.
3. **An optional Tier-2 side-channel** (headers/query) should be specified but not required.

It also pinned **scope** ([`harness:30-31`](findings/2026-06-29-harness-intent-routing.md)):
**Cursor** is backend-mediated + Verify-gated (Tier 0 / unusable for self-hosted routing);
**Amp / Devin / closed SaaS** cannot be repointed at a custom endpoint at all (**none — cannot reach
anvil-serving**).

**What it changed.** It confirmed the wire surface ("model-name-as-intent" is right, as a **closed
preset enum** — a flat string can't carry multi-axis intent) **and** forced the Tier-0 classifier to
be load-bearing rather than optional. It set the tier ladder (§2.3) and drew the scope boundary
(§4). Net: presets are the *declarative ceiling*, the classifier is the *operating mode for most
traffic*.

### 3.4 OpenClaw vs Hermes — the per-request beachhead

**Source:** [`findings/2026-06-29-openclaw-hermes-customization.md`](findings/2026-06-29-openclaw-hermes-customization.md)
(research) + [`OPENCLAW-INTEGRATION-SPEC.md`](OPENCLAW-INTEGRATION-SPEC.md) (source-verified
buildable spec, verdict **go-with-caveats**, MEDIUM risk).

**What it asked.** Do OpenClaw or Nous Hermes-Agent expose router-relevant customization (especially
per-request hooks) beyond the closed harnesses?

**What it found** ([`openclaw-hermes:34-49`](findings/2026-06-29-openclaw-hermes-customization.md)):

- **OpenClaw — YES (HIGH confidence).** Its `before_model_resolve` hook receives the prompt +
  attachment metadata and can return `providerOverride`/`modelOverride`. A local plugin classifies
  each turn and overrides the resolved model **verbatim** — making the Tier-1 model-string carrier
  **per-request**, escaping the session-coarse limit. It is open-source (TS Plugin SDK), and one
  integration reaches the many chat surfaces OpenClaw already bridges (WhatsApp/Telegram/Discord/
  Slack/Signal/iMessage). It does **not** expose a per-request arbitrary header/body channel (only
  static config-level), so it's per-request *routing*, not a structured Tier-2 side-channel.
- **Hermes Agent — NO over the wire (HIGH confidence).** Stronger Tier-1 than Claude Code
  (first-class custom base_url, verbatim free-form models, **many more slots** — main + ~11 aux +
  fallback/MoA/subagents), but its hooks **explicitly cannot modify the outgoing request**;
  `pre_llm_call` only injects user-message text (which reaches the router as Tier-0-parseable
  content). Confirmed by an open, unmerged feature request (#23739 / PR #23898) asking for exactly
  this capability. True Tier 2 requires a fork.

The source-level spec refined one point and flagged two **CRITICAL validate-first gaps** before any
build code is written ([`OPENCLAW-INTEGRATION-SPEC.md:24-46`](OPENCLAW-INTEGRATION-SPEC.md)):
`before_model_resolve` fires **once per run, above the attempt loop** (not literally "per turn") — so
the "classify each user message" premise must be confirmed live; and **(1)** the outbound wire
`model` value (bare `planning` vs full `anvil/planning` — anvil should accept **both**) and **(2)**
the firing cadence must be captured on a live gateway.

**What it changed.** **OpenClaw-first beachhead — focus, not couple** (§2.4). Keep Tier 0 (infer) as
the floor and Tier 1 (verbatim free-form preset in the `model` field) as the primary contract; add an
explicit **Tier-2 "hook plugin" seam** where a published `before_model_resolve` plugin pushes Tier-0
classification to the **client** (cheaper, better-informed — it sees full turn context) and turns the
router into a clean executor ([`openclaw-hermes:50-66`](findings/2026-06-29-openclaw-hermes-customization.md)).
Hermes shows the alternative (payload text injection) collapses back to Tier-0 parsing, so it is
supported as a strong Tier-1 client but deferred as a Tier-2 target. The integration depth on OpenClaw
is gated on resolving the two live gaps first.

---

## 4. What this means for scope

**In scope** (any client that allows a custom base URL **and** a free-form model string):

- Claude Code (`ANTHROPIC_BASE_URL`), OpenAI Codex CLI, Aider (`openai/<preset>`), Cline, Continue.dev
  — all get Tier 0 (infer) + Tier 1 (presets) for free from the protocol-standard core.
- **OpenClaw** — near-first-class beachhead; the one client that reaches per-request intent via the
  `before_model_resolve` hook.
- **Hermes Agent** — strong Tier-1 client (rich multi-slot); deferred as a Tier-2 target.

**Out of scope** ([`QUALITY-GATED-ROUTER.md:176-179`](QUALITY-GATED-ROUTER.md),
[`prd:48-59`](prd/anvil-serving-harness-router.prd.md)):

- **Cursor** (agent/Composer) — backend-mediated through Cursor's own backend + a Verify gate; not
  usable for self-hosted routing.
- **Amp / Devin / closed SaaS agents** — backend-locked, cannot be repointed at a custom endpoint at
  all.
- **Routing Anvil's own planning to local for quality** — the eval (§3.2) shows the gap; planning is
  free on the subscription; **planning/critic stay cloud-default (failover-only)** and are **never
  auto-promoted on quality grounds** ([`prd:50-52,105-107`](prd/anvil-serving-harness-router.prd.md)).
  The surviving sliver of bake-off T013 — single-endpoint planning **failover** if a cloud model goes
  dark — is kept only in the runbook appendix
  ([`../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md:167-175`](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md)).
- **Coupling to Anvil or to OpenClaw** — the core speaks standard wire protocols; OpenClaw is an
  adapter, not a dependency.
- **A public third-party plugin SDK at MVP** — internal typed seams now (trusted-only); public entry
  points with signing/allowlisting deferred to M3+.
- **Per-internal-step intent** — harnesses can't carry it; per-user-message ("per run") is the
  cadence the router targets.
- **Building a new serving substrate** — `profile`, `models sync`, `analyze`, `multiplexer`,
  `preflight`, `benchmark`, and the shadow-eval harness already exist and are **reused** (§5).

---

## 5. The plan

Tracked as the Anvil PRD **`harness-router`** → **v0.3.0, 18 tasks**
([`prd/anvil-serving-harness-router.prd.md`](prd/anvil-serving-harness-router.prd.md)). The PRD rolled
in the remaining bake-off work: the runtime router (bake-off **T010**) is **promoted to the core**;
"wire both tiers into Anvil" (bake-off **T013**) is **retired**; the two-week measure / decide-gate
(bake-off **T014/T015**) are **reframed** as the router's per-work-class validation + promotion gate
([`prd:513-520`](prd/anvil-serving-harness-router.prd.md)).

**Milestones** (mirror [`QUALITY-GATED-ROUTER.md:264-277`](QUALITY-GATED-ROUTER.md);
[`prd:170-178`](prd/anvil-serving-harness-router.prd.md)). **Shippable MVP = M0–M2** on the
hand-authored profile table ("useful and unique"); **M3** is the moat (the measured table):

- **M0 — front door + config** (T001, T002): Anthropic + OpenAI endpoints, streaming, pass-through to
  one backend; the tier/topology config schema + worked example. Makes anvil-serving drop-in for
  Claude Code today.
- **M1 — intent + policy, hand-seeded** (T003–T006): preset parsing + Tier-0 classifier + `model:`
  override; `/v1/models` discovery; the hand-authored quality-profile store + residency-aware routing
  policy; cloud-tier credentials on the Backend seam + secrets hygiene.
- **M2 — the wedge** (T007–T014): cheap structural verify; the **streaming commit-window spike** (the
  named "spike-this-early" risk — buffer-verify-then-commit so a local miss never delivers partial
  tokens); tier fallback with thrash + budget guards + decision logging; transparent responses; typed
  extension seams; the `serve` CLI verb; **OpenClaw live-gap validation (T013) then the reference
  adapter plugin (T014)**. First release delivering the unique promise.
- **M3 — the moat** (T015–T018): bootstrap the quality profile from the shadow-eval harness; async
  calibration (opt-in + redaction) + fingerprint staleness; validate on real routed traffic
  (silent-failure rate target < 1%); write the per-work-class promotion decision (planning/critic
  documented cloud-default regardless of throughput pressure).

**Success metrics** ([`QUALITY-GATED-ROUTER.md:298-304`](QUALITY-GATED-ROUTER.md)): **% of agent
traffic safely served local** at a **bounded rework rate**; **silent-failure rate ≈ 0**; **cloud
tokens saved** vs all-cloud at constant accept-rate; **drop-in time** (minutes from `pip install` to a
harness running through it).

**Reuse map — most of this already exists**
([`QUALITY-GATED-ROUTER.md:250-261`](QUALITY-GATED-ROUTER.md)): `profile` (right-size from real usage),
`models sync` / `analyze` (per-model serving facts), `multiplexer` (single-resident swap on one GPU —
already proves the per-engine SGLang+vLLM dispatch-behind-one-interface pattern), `preflight`
(correctness gate), `benchmark` (capacity), and the **shadow-eval harness** (built — generalize from
"planning" to arbitrary work-classes). The genuinely-new surface is the **router data plane + the
quality-profile control plane**.

### Pointers

| Document | Role |
|---|---|
| [`QUALITY-GATED-ROUTER.md`](QUALITY-GATED-ROUTER.md) | The product design (thesis, architecture, profile, tier ladder, verify/fallback, seams, milestones, risks) |
| [`OPENCLAW-INTEGRATION-SPEC.md`](OPENCLAW-INTEGRATION-SPEC.md) | Source-verified buildable OpenClaw spec + the two validate-first gaps |
| [`prd/anvil-serving-harness-router.prd.md`](prd/anvil-serving-harness-router.prd.md) | The tracked PRD — requirements R001–R014, features F001–F009, tasks T001–T018, bake-off provenance |
| [`findings/2026-06-28-anvil-integration-audit.md`](findings/2026-06-28-anvil-integration-audit.md) | Evidence: the integration point is the runtime, not Anvil |
| [`findings/2026-06-28-planning-capability-eval.md`](findings/2026-06-28-planning-capability-eval.md) | Evidence: local quality is work-class-dependent and measurable (the seed of the whole thesis) |
| [`findings/2026-06-29-harness-intent-routing.md`](findings/2026-06-29-harness-intent-routing.md) | Evidence: the `model` string is the only routing channel; tier ladder; scope |
| [`findings/2026-06-29-openclaw-hermes-customization.md`](findings/2026-06-29-openclaw-hermes-customization.md) | Evidence: OpenClaw's per-request hook → the beachhead |
| [`../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md`](../examples/fakoli-dark/BAKE-OFF-RUNBOOK.md) | The bake-off this work supersedes; surviving sliver = planning failover (appendix) |
