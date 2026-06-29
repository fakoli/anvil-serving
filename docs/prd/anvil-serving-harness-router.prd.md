# Project: anvil-serving harness router

## Summary

anvil-serving becomes a **harness-facing, workload-aware, correctness-gated local-model router** for
coding harnesses (Claude Code, Codex, Aider, Cline, Continue, and OpenClaw as the **near-first-class**
beachhead). A harness points at **one** anvil-serving endpoint; per request the router resolves an
**intent** (a named-preset "use-case", carried in the `model` field — model-name-as-intent) to a
**tier** (fast-local / heavy-local / cloud) using a **measured per-(model, work-class) quality
profile**, cheaply **verifies** the output, and **falls back** to cloud on failure. The harness sees
one reliable endpoint and never eats a silent local-quality failure mid-run. The core stays
**protocol-standard** (Anthropic Messages + OpenAI Chat Completions) with **zero OpenClaw import**;
OpenClaw gets the deep integration as a **thin, swappable adapter** (focus, not couple). This PRD
supersedes the remaining bake-off tasks: the runtime router (**bake-off T010**) is promoted to the
core of this work, "wire both tiers into Anvil" (**bake-off T013**) is retired (anvil is a state
engine, not an LLM gateway), and the two-week measure / decide-gate (**bake-off T014/T015**) are
reframed as the router's per-work-class validation and promotion gate. Design basis:
`docs/QUALITY-GATED-ROUTER.md`, `docs/OPENCLAW-INTEGRATION-SPEC.md`, and the four findings dated
2026-06-28/29.

**Work-class taxonomy.** "Work-class" is the routing key (see `QUALITY-GATED-ROUTER.md` §6, taxonomy
v0: `chat/Q&A`, `bounded-edit`, `multi-file-refactor`, `planning/decomposition`, `review/critique`,
`long-context-retrieval`). The R002 preset enum is the **coarse wire proxy** a caller can declare; when
no preset is declared the Tier-0 classifier emits a work-class, which the routing policy maps to a
tier. Profile/validation/promotion (R003/R009/R010) are keyed on work-class.

**Release:** v0.3.0

## Goals

- One drop-in endpoint any "custom base URL + free-form model string" harness can use today (Tier 0
  inference floor + Tier 1 named presets), with OpenClaw as the deep per-request-intent beachhead.
- Route each request to the cheapest tier **proven by measurement** to handle its work-class; never
  silently ship a local-quality failure (cheap verify + automatic cloud fallback), **including the
  streaming default path**.
- Make routing **evidence-based**: a per-(model, work-class) quality profile, hand-seeded for MVP and
  later bootstrapped from the shadow-eval harness and continuously calibrated off the hot path.
- Expose an **intent-addressed** API (named presets), not model names, with **transparent responses**
  (the response reports the real tier that served) and `/v1/models` preset discovery.
- Keep the core **protocol-standard with zero OpenClaw coupling**; the OpenClaw integration is a thin,
  swappable adapter plugin that can be replaced without touching the router.
- Be **deployable**: a config surface declaring the tier topology + credentials, and a first-class CLI
  `serve` verb, so a user goes from `pip install` to a running router.
- **Decide, per work-class**, which work runs local — gated by a measured quality bar plus real
  capacity relief — never all-or-nothing, and never auto-promoting planning/critic on quality grounds.

## Non-Goals

- Routing **Anvil's own planning** to local for quality — the planning eval shows the gap and planning
  is free on the subscription; local-planning stays **failover-only** (see the bake-off runbook).
- Coupling to anvil **or** to OpenClaw — the core router speaks standard wire protocols; OpenClaw is an
  adapter, not a dependency.
- Supporting **backend-locked harnesses** (Cursor agent/Composer, Amp, Devin) for self-hosted routing.
- A **public third-party plugin SDK** at MVP — internal typed seams now (trusted-only); public entry
  points with signing/allowlisting deferred.
- **Per-internal-step** intent — harnesses can't carry it; per-user-message ("per run") is the cadence
  the router targets.
- Building a new serving substrate — `profile`, `models sync`, `analyze`, `multiplexer`, `preflight`,
  `benchmark`, and the shadow-eval harness already exist and are reused.

## Requirements

- R001: A single front door speaks both the Anthropic Messages and OpenAI Chat Completions dialects
  over a custom base URL, streams (SSE for both framings), and is drop-in for Claude Code
  (`ANTHROPIC_BASE_URL`) and OpenAI-compatible clients with no harness code changes.
- R002: The request `model` field is interpreted as an **intent** — a closed named-preset enum
  (`planning`, `quick-edit`, `review`, `chat`, `long-context`) accepted both bare and as
  `anvil/<preset>`; a Tier-0 classifier infers the work-class when no preset is declared; a
  `model:`-pin override is honored; ambiguous classifications bias to the safer/cloud tier.
- R003: A per-(model, work-class) **quality profile** maps each class to a decision
  ∈ {`allow`, `allow-with-verify`, `deny`}. **MVP** seeds it with a **hand-authored static table** plus a
  routing policy that filters the candidate tier pool by hard constraints (context, privacy, tool
  support) and ranks survivors by the profile. **Post-MVP** the profile is keyed on a **serve
  fingerprint** (model + quant + engine + serve flags), bootstrapped from the shadow-eval harness,
  right-sized from real usage via `profile`, and continuously **calibrated** from sampled production
  traffic graded off the hot path; a fingerprint change marks affected rows stale and triggers
  re-measure. (MVP vs post-MVP split is tracked in the Milestones section + task tagging.)
- R004: Cheap **inline structural verification** (empty/truncated content, invalid tool-call JSON,
  unparseable code, non-applying diff, malformed format) plus confidence signals; on
  verify-fail / error / timeout / low-confidence the router **falls back** up the tier chain to cloud.
  For the **streaming default path**, fail-prone / `allow-with-verify` classes use a non-streamed
  **commit window** (buffer-verify-then-commit) so a local miss never delivers partial tokens to the
  harness. Guardrails: retry caps, circuit breakers, and a **per-session/window cost budget** that
  stops escalating to cloud once hit. Every fallback is logged as a profile signal.
- R005: **Transparent responses** — the response reports the real model/tier that served and whether it
  fell back; a decision log records (declared-or-inferred intent → work-class → tier → verify result →
  fallback?) **plus per-request/per-tier token accounting** (prompt/completion tokens and the
  counterfactual all-cloud cost) emitted via the Observer seam. Heavy LLM grading is async/sampled,
  never an inline blocking gate.
- R006: `/v1/models` advertises the preset vocabulary (token + human name + description) for picker
  discovery; session **stickiness** uses turn-level switch boundaries; intent→tier mapping is versioned
  and **pinnable** for reproducibility.
- R007: **OpenClaw beachhead** (near-first-class — depth gated on live validation, since the supporting
  facts are vendor-doc-level) — a reference `before_model_resolve` adapter plugin classifies the user
  message and emits a preset id as `modelOverride`; the router core contains **zero OpenClaw-specific
  code**; the two CRITICAL live gaps (outbound wire `model` value; per-run firing cadence) are validated
  on a live gateway **before** the plugin ships; the OpenClaw release + `pluginApi` compat are pinned.
- R008: The router exposes **typed internal extension seams** (`Dialect`, `Classifier`,
  `RoutingPolicy`, `Backend`, `Verifier`, `Grader`, `ProfileStore`, `Observer`) as Protocols + an
  in-process registry; a seam implementation that raises/times out is isolated and treated as a fallback
  trigger; in-process seams are **trusted-only** until the deferred public SDK adds signing/allowlisting.
- R009: The router is **validated on real routed traffic** (reframed bake-off T014): per-work-class
  accept/rework rate, silent-failure rate, cloud-tokens saved vs all-cloud (from R005 token accounting),
  and drop-in time, sliced by routing decision.
- R010: A **per-work-class promotion decision** (reframed bake-off T015): a work-class is routed local
  only if it clears its measured quality bar **and** relieves measured capacity pressure; results are
  written up; planning/critic are never auto-promoted on quality grounds.
- R011: A **router config schema** declares the tier topology — per tier: `id`, `base_url`, backend
  dialect, context limit, privacy class, tool support, auth ref — plus the preset→candidate-pool mapping
  and mapping version; `configs/example.toml` carries a worked router block. The routing policy's
  candidate pool is loaded from this config.
- R012: **Outbound cloud-tier credentials + secrets hygiene** — the user supplies the Anthropic/OpenAI
  key via env/config referenced per-tier on the Backend seam; the decision log (R005) and metrics (R009)
  **never persist secrets or full prompts** unless calibration opt-in is explicitly set.
- R013: **Tier availability/residency** feeds the RoutingPolicy — the local fast/heavy tiers are a
  single-resident swap pair on one GPU (`multiplexer.py`), so the policy considers residency and defines
  non-resident behavior (pay the swap cost vs skip-to-cloud) and must not thrash the multiplexer under
  mixed-class traffic.
- R014: The router is a **first-class product surface** — an `anvil-serving serve --config ...` CLI verb
  loads the tier config, binds backends/the multiplexer, and starts the front door.

## Features

### F001: Protocol-standard front door
**Requirements:** R001

### F002: Intent resolution (presets + Tier-0 classifier + override)
**Requirements:** R002

### F003: Quality profile & routing policy
**Requirements:** R003, R013

### F004: Verify-and-fallback safety net (incl. streaming commit window)
**Requirements:** R004

### F005: Transparency, discovery & session stickiness
**Requirements:** R005, R006

### F006: OpenClaw beachhead integration
**Requirements:** R007

### F007: Internal extension seams
**Requirements:** R008

### F008: Validation & per-work-class decide-gate
**Requirements:** R009, R010

### F009: Config, credentials & launch surface
**Requirements:** R011, R012, R014

## Risks

- Mid-stream fallback (the named "spike-this-early" risk): verify-before-deliver fights low-latency SSE
  streaming. Owned by T008 (commit-window spike) — not left to swell T001/T009.
- OpenClaw API churn: young CalVer project, extension surface mid-refactor; the targeted hook could
  shift. Mitigated by the zero-import core + thin swappable adapter + pinned versions.
- Classification accuracy: wrong work-class → wrong route. Mitigated by a coarse taxonomy, safer-tier
  bias on ambiguity, and continuous calibration.
- Single-resident multiplexer thrash under mixed-class traffic (R013).
- Privacy: async cloud calibration samples local traffic to a cloud grader — must be opt-in and
  redactable (T016).

## Open Questions

- The two OpenClaw live gaps (wire `model` value; per-run firing cadence) — resolved by T013 hands-on
  validation, not by planning.
- Preset vocabulary size: start at the five in R002; expand only when measurement shows a class needs
  splitting.

## Milestones / MVP cut

Mirrors `QUALITY-GATED-ROUTER.md` §12. **Shippable MVP = M0–M2 on the hand-authored profile table**
("useful and unique"); M3 ("the moat") is the measured table + calibration + validation/decide.

- **M0 — front door + config:** T001, T002.
- **M1 — intent + policy (hand-seeded):** T003, T004, T005, T006.
- **M2 — the wedge (verify + fallback + transparency + OpenClaw + CLI):** T007, T008, T009, T010, T011, T012, T013, T014.
- **M3 — the moat (measured + calibrated + validated):** T015, T016, T017, T018.

## Tasks

### T001: Stand up the protocol-standard front door with streaming

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/router/front_door.py, anvil_serving/router/dialects/anthropic.py, anvil_serving/router/dialects/openai.py

Provide one HTTP endpoint that accepts both Anthropic Messages and OpenAI Chat Completions requests, translates each to a common internal request, and streams responses back in the caller's native SSE framing. Drop-in for Claude Code via `ANTHROPIC_BASE_URL` and OpenAI-compatible clients, passing through to one configured backend initially. (M0)

**Acceptance criteria:**

- A Claude Code (Anthropic Messages) request and an OpenAI Chat Completions request both receive a correct streamed response through the endpoint.
- Both SSE framings (Anthropic named events; OpenAI `data:`/`[DONE]`) are emitted correctly.

**Verification:**

- `pytest tests/router/test_front_door.py`
- `curl -N http://127.0.0.1:8000/v1/chat/completions -d '{"model":"chat","messages":[{"role":"user","content":"hi"}],"stream":true}'`

### T002: Define the router/tier config schema + worked example

**Feature:** F009
**Priority:** high
**Likely files:** anvil_serving/router/config.py, configs/example.toml
**Dependencies:** T001

Define the config that declares the tier topology: per tier `id`, `base_url`, backend dialect, context limit, privacy class, tool support, and an auth reference (env var name); plus the preset→candidate-pool mapping and a mapping version. Extend `configs/example.toml` with a worked router block. (M0)

**Acceptance criteria:**

- Loading `configs/example.toml` yields the fast-local, heavy-local, and cloud tiers with their endpoints, dialects, and constraints.
- Each tier's auth reference names an env var; no secret literal appears in the config or is required to load it.

**Verification:**

- `pytest tests/router/test_config.py`
- `python -c "from anvil_serving.router.config import load; print([t.id for t in load('configs/example.toml').tiers])"`

### T003: Implement intent resolution — presets, Tier-0 classifier, override

**Feature:** F002
**Priority:** high
**Likely files:** anvil_serving/router/intent.py, anvil_serving/router/classify.py
**Dependencies:** T001, T002

Interpret the `model` field as a named-preset intent (accepted bare and as `anvil/<preset>`). When no known preset is supplied, run the cheap Tier-0 classifier over the payload to assign a work-class. Honor an explicit model pin. Bias ambiguous classifications to the safer/cloud tier. (M1)

**Acceptance criteria:**

- `model: "planning"` and `model: "anvil/planning"` resolve to the same intent.
- An unknown/empty model is assigned a work-class by the classifier and never errors.
- Ambiguous inputs resolve to the configured safer tier, recorded in the decision log.

**Verification:**

- `pytest tests/router/test_intent.py tests/router/test_classify.py`

### T004: Serve `/v1/models` preset discovery

**Feature:** F005
**Priority:** medium
**Likely files:** anvil_serving/router/discovery.py
**Dependencies:** T003

Expose `/v1/models` listing the preset tokens with human-readable names and descriptions so presets surface in harness pickers. (M1)

**Acceptance criteria:**

- `/v1/models` returns each preset with `id`, `name`, and `description`, matching the R002 enum (no drift).

**Verification:**

- `curl -s http://127.0.0.1:8000/v1/models | python -m json.tool`
- `pytest tests/router/test_discovery.py`

### T005: Build the quality-profile store + routing policy (residency-aware)

**Feature:** F003
**Priority:** high
**Likely files:** anvil_serving/router/profile_store.py, anvil_serving/router/policy.py
**Dependencies:** T002, T003

Implement the per-(model, work-class) table with {allow, allow-with-verify, deny}, seeded hand-authored (R003). The policy loads the candidate pool from config (T002), filters by hard constraints (context/privacy/tool support), incorporates a tier availability/residency signal (single-resident swap pair, R013), and ranks survivors by the profile. Define the serve-fingerprint composition for later staleness detection (R003). (M1)

**Acceptance criteria:**

- Given an intent + the profile, the policy returns an ordered candidate tier list; a `deny` class for a tier is never routed there.
- The candidate pool is loaded from config (T002), not hard-coded.
- Under an alternating fast/heavy mixed-class request sequence, the policy does not trigger a multiplexer swap on every request (a residency-aware test asserts swap count stays bounded / skips to a resident or cloud tier).

**Verification:**

- `pytest tests/router/test_policy.py tests/router/test_residency.py`

### T006: Cloud-tier credentials on the Backend seam + secrets hygiene

**Feature:** F009
**Priority:** high
**Likely files:** anvil_serving/router/backends/cloud.py, anvil_serving/router/secrets.py
**Dependencies:** T002

Supply outbound cloud-provider credentials (Anthropic/OpenAI) via the per-tier auth ref from config, used by the cloud Backend. Guarantee the decision log and metrics never persist secrets or full prompts unless calibration opt-in is set. (M1)

**Acceptance criteria:**

- A cloud-tier request authenticates using the key resolved from the configured env var; a missing key yields a clear startup/config error, not a silent failure.
- A redaction test asserts no API key and no full prompt body appears in the decision log or metrics output with calibration off.

**Verification:**

- `pytest tests/router/test_cloud_auth.py tests/router/test_secrets_hygiene.py`

### T007: Implement cheap inline structural verification

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/router/verify.py
**Dependencies:** T001

Add near-zero-cost structural checks on responses (empty/truncated content, invalid tool-call JSON, unparseable code, non-applying diff, malformed format) plus confidence signals. Verifiers are a chainable seam (T011). (M2)

**Acceptance criteria:**

- Each check has a unit test with a passing and a failing fixture.
- A structural assertion confirms the verify path makes no network/LLM call (it is purely local/structural).

**Verification:**

- `pytest tests/router/test_verify.py`

### T008: Spike the streaming commit-window for fail-prone classes

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/router/commit_window.py
**Dependencies:** T007

De-risk the hardest data-plane problem: for `allow-with-verify` / fail-prone classes, buffer output to a non-streamed commit window before the first byte reaches the harness, run structural verify, then commit-and-stream or fall back cleanly. Define the TTFT/latency budget and which work-classes get the window. (M2)

**Acceptance criteria:**

- A forced mid-stream local verify-failure yields a cloud-served response with **no partial local tokens delivered to the harness**.
- A class not flagged fail-prone streams normally (the commit window is not applied), documented by a test.

**Verification:**

- `pytest tests/router/test_commit_window.py`

### T009: Tier fallback with thrash + budget guards and decision logging

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/router/fallback.py, anvil_serving/router/decision_log.py
**Dependencies:** T005, T006, T007, T008

On verify-fail / error / timeout / low-confidence, retry up the tier chain to cloud. Cap retries, circuit-break repeat offenders, enforce a per-session/window cost budget, keep fallback idempotent, and log every fallback (with per-request/per-tier token accounting, R005) as a profile signal. (M2)

**Acceptance criteria:**

- A forced local verify-failure results in a cloud-served response with the fallback + token counts recorded.
- Sustained failure respects the retry cap, the circuit breaker, and the per-session cost budget (it stops escalating once the budget ceiling is hit).

**Verification:**

- `pytest tests/router/test_fallback.py tests/router/test_budget.py`

### T010: Transparent responses + decision log

**Feature:** F005
**Priority:** high
**Likely files:** anvil_serving/router/decision_log.py
**Dependencies:** T009

Set the response `model`/metadata to the real model/tier that served (and fallback status), and emit the full decision record (intent → work-class → tier → verify → fallback) with token accounting via the Observer seam. (M2)

**Acceptance criteria:**

- Response metadata names the actual served tier and whether a fallback occurred.
- The decision log line for a request carries intent, work-class, served tier, verify result, fallback flag, and prompt/completion token counts.

**Verification:**

- `pytest tests/router/test_transparency.py`

### T011: Define typed extension seams (Protocols + registry)

**Feature:** F007
**Priority:** medium
**Likely files:** anvil_serving/router/seams.py, anvil_serving/router/registry.py
**Dependencies:** T003, T005, T007

Express Dialect/Classifier/RoutingPolicy/Backend/Verifier/Grader/ProfileStore/Observer as Protocols + an in-process registry. A seam implementation that raises/times out is isolated and converted to a fallback trigger. Seams are trusted-only; no public entry points. (M2)

**Acceptance criteria:**

- Each seam has a Protocol and at least one registered implementation resolved via the registry.
- A deliberately-throwing verifier is caught and converted to a fallback, not a crash.

**Verification:**

- `pytest tests/router/test_seams.py`

### T012: Expose the router as a first-class CLI `serve` verb

**Feature:** F009
**Priority:** medium
**Likely files:** anvil_serving/cli.py, anvil_serving/router/serve.py
**Dependencies:** T001, T002, T005

Add `anvil-serving serve --config <path>` that loads the tier config, binds backends + the multiplexer, and starts the front door — the pip-install-to-running surface. (M2)

**Acceptance criteria:**

- `anvil-serving serve --help` documents the verb; `anvil-serving serve --config configs/example.toml` starts the front door bound to the configured tiers.
- A smoke test measures pip-install-to-first-served-request (drop-in time) and records it.

**Verification:**

- `anvil-serving serve --help`
- `pytest tests/router/test_serve_cli.py`

### T013: Validate the two CRITICAL OpenClaw live gaps (with a logging hook)

**Feature:** F006
**Priority:** high
**Likely files:** examples/openclaw/logging-hook/index.ts, examples/openclaw/validate.py
**Dependencies:** T001, T003

Before building the routing plugin: use the existing OpenClaw install on **Fakoli Mini** (the gateway box — no fresh stand-up; confirm/pin its version), point a custom provider at the front door, and install a **minimal logging-only `before_model_resolve` hook** (a T013 sub-deliverable, resolving the chicken-and-egg with T014). Capture a real outbound request (gap a) and log every hook fire with run/session ids across a multi-turn conversation (gap b). (M2)

**Acceptance criteria:**

- A captured outbound request's `model` string matches `^(anvil/)?(planning|quick-edit|review|chat|long-context)$`, and the front door accepts both forms.
- Over a logged multi-turn session, hook-fire-count == user-message-count (or the actual cadence is documented in the spec).

**Verification:**

- `python examples/openclaw/validate.py --assert-wire-form --assert-fire-cadence examples/openclaw/hook-fire-log.jsonl`

### T014: Ship the reference OpenClaw `before_model_resolve` adapter plugin

**Feature:** F006
**Priority:** high
**Likely files:** plugins/openclaw-anvil-intent-router/index.ts, plugins/openclaw-anvil-intent-router/package.json
**Dependencies:** T013, T003

Build the ~50-line adapter plugin that classifies the user message and returns a preset as `modelOverride` (provider `anvil`). Pin `pluginApi` compat + the OpenClaw release. Keep all OpenClaw-specific code in this package; document the `security.installPolicy`/`plugins.allow` install path. (M2)

**Acceptance criteria:**

- With the plugin installed, an OpenClaw run (one user message) routes to the anvil endpoint with the expected preset as the wire model, asserted from the decision log.
- The router core (`anvil_serving/router/`) contains zero OpenClaw references.

**Verification:**

- `if grep -riq openclaw anvil_serving/router/; then echo COUPLING-FOUND; exit 1; fi`
- `jq -e 'select(.source=="openclaw" and .intent=="planning")' decision_log.jsonl`

### T015: Bootstrap the quality profile from the shadow-eval harness

**Feature:** F003
**Priority:** medium
**Likely files:** anvil_serving/router/profile_bootstrap.py, docs/findings/eval-data/
**Dependencies:** T005

Generalize the shadow-eval harness to replay representative requests per work-class to each tier, grade against cloud, and emit the profile table — replacing the hand-authored seed (R003). Provide a `--replay <fixtures>` mode that bootstraps from recorded eval data for CI. (M3)

**Acceptance criteria:**

- `--replay` over committed fixtures produces a profile table with a quality score + sample count per (model, work-class) and loads into the store.
- The live run (against real tiers) is a separately-labeled integration step.

**Verification:**

- `python -m anvil_serving.router.profile_bootstrap --replay docs/findings/eval-data/ --out profile.json && pytest tests/router/test_profile_bootstrap.py`

### T016: Async calibration sampler (opt-in + redaction) + fingerprint staleness

**Feature:** F003
**Priority:** medium
**Likely files:** anvil_serving/router/calibrate.py, anvil_serving/router/fingerprint.py
**Dependencies:** T010, T015

Add an off-hot-path sampler that grades a small % of responses against cloud to update the profile, default-off and opt-in, with a redaction pass before any payload leaves the box. Detect serve-fingerprint changes and mark affected rows stale. (M3)

**Acceptance criteria:**

- The response is returned to the caller before the grader future is awaited (sampler runs on a background task).
- With calibration off, nothing is sampled; with it on, redaction removes the configured sensitive fields before egress.
- A simulated serve-fingerprint change marks the affected profile rows stale.

**Verification:**

- `pytest tests/router/test_calibrate.py tests/router/test_fingerprint.py`

### T017: Validate on real routed traffic (reframed bake-off T014)

**Feature:** F008
**Priority:** medium
**Likely files:** anvil_serving/router/metrics.py, docs/findings/
**Dependencies:** T009, T010

Drive real harness traffic through the router and measure, per work-class: accept/rework rate, silent-failure rate, cloud-tokens saved vs all-cloud (from R005 token accounting), and drop-in time — sliced by routing decision. (M3)

**Acceptance criteria:**

- A metrics report emits per-work-class accept-rate, silent-failure rate, and cloud-tokens-saved over a traffic window.
- Silent-failure rate is below a configured `silent_failure_threshold` (target < 1%), asserted against a committed fixture traffic window for CI.

**Verification:**

- `pytest tests/router/test_metrics.py`
- `python -m anvil_serving.router.metrics --replay tests/router/fixtures/traffic.jsonl`

### T018: Write the per-work-class promotion decision (reframed bake-off T015)

**Feature:** F008
**Priority:** medium
**Likely files:** docs/findings/
**Dependencies:** T017

From the measured data, decide per work-class which run local — promote a class only if it clears its quality bar and relieves measured pressure. Document the decision; never auto-promote planning/critic on quality grounds. (M3)

**Acceptance criteria:**

- A written decision lists each work-class with its measured quality and the local/cloud verdict + rationale.
- Planning/critic are documented as cloud-default (failover-only) regardless of throughput pressure.

**Verification:**

- `f=$(ls docs/findings/*-router-promotion-decision.md) && grep -qi planning "$f" && grep -qiE "cloud-default|failover-only" "$f"`

## Provenance — rolled-in bake-off tasks (traceability)

| Bake-off task | Disposition in this PRD |
|---|---|
| **bake-off T010** runtime router spec | Promoted to the **core** of this PRD — exploded into F001–F007/F009 and most tasks. |
| **bake-off T013** wire both tiers into Anvil | **Retired.** Anvil is a state engine, not an LLM gateway; surviving sliver = single-endpoint planning **failover**, kept in `examples/fakoli-dark/BAKE-OFF-RUNBOOK.md` (Appendix), not here. |
| **bake-off T014** two-week measure | **Reframed** → R009 / F008 / **T017** (per-work-class validation on real traffic). |
| **bake-off T015** decide-gate | **Reframed** → R010 / F008 / **T018** (per-work-class promotion decision). |
