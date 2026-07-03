# ADR-0009 — Measured quality-profile write-back loop (offline-batch-first, fingerprint-keyed)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Relates to:** ADR-0001 (cloud cost & subscription auth), ADR-0003 (portable defaults / generated bring-up), ADR-0007 (subscription-auth cloud tier via `claude` CLI subprocess); supersedes nothing.

## Context

The router's intended moat is a **measured** per-`(tier, work_class)` quality profile that decides routing. Today it is not measured, and no measured data ever reaches routing:

- The deployed config has no `[router].profile_path` (commented out in `configs/example.toml`), so `serve.build_server` (serve.py:680-704) falls to `profile_store.default_profile()` — hand-authored SEED verdicts with `last_measured=None`.
- `quality_score` is advisory and UNUSED for ordering: `policy.route` preserves config cost order (policy.py:270) and never reads `score()`; only the `decision()=="deny"` gate is load-bearing (policy.py:258).
- `ProfileStore.record_grade` is called ONLY from `calibrate.Calibrator`, which is never instantiated in production; the Calibrator's grader is an INJECTED callable and **no concrete product grader exists anywhere** (calibrate.py:31-32, 81, 199).
- `fingerprint.apply/refresh` is never invoked in the serving path, so the serve-identity staleness machinery is dormant.
- `profile_bootstrap.run_live()` is guarded AND raises `NotImplementedError` (profile_bootstrap.py:425-436). Only `--replay` (committed judge fixtures -> `profile.json`) works, and its `store_from_profile` drops `stale`/`fingerprint` on load and builds a table from ONLY the doc's rows.

Two consequences bite. First, local `planning` is categorically DENIED by a hand-authored seed and never re-measured — and in the deployed (local-only) config that yields an empty gated pool -> 503, so "planning must go cloud" is only realizable with the cloud config. Second, the one live measurement path that exists (the dated eval generator) hardcodes stale model ids and bypasses the router's `extra_body`, so it measures thinking-ON while prod serves thinking-OFF — a measurement of the wrong thing.

Hard constraints (project golden rules): (1) NO SELF-VERIFICATION — the judge must be independent of the model that produced the output; (2) AGENT SDK, NOT RAW API — any model-calling judge uses the Claude Agent SDK on the subscription, never `api.anthropic.com` with a key; (3) STDLIB-ONLY hot path; (4) DATA-DRIVEN RE-MEASURE — no routing change without a reviewable A/B; (5) ADR discipline.

A cross-cutting design question: the profile must eventually let the router choose a per-work-class REASONING policy (thinking off/low for chat/quick-edit, on for planning/review), but the schema keys on `(tier_id, work_class)` with no reasoning axis, and the reasoning knob rides in `Tier.extra_body` — which `serve_fingerprint` does NOT currently capture (fingerprint.py:54 synonyms omit `extra_body`).

## Considered options

**A. Offline batch calibration ("bootstrap-and-review").** Implement `run_live` as an on-demand CLI verb: generate through each tier's REAL backend (so `Tier.extra_body`/thinking-off is byte-identical to prod), grade with an independent Agent-SDK Claude judge over the committed DIMS /25 rubric, write a fingerprint-tagged candidate `profile.json` the operator diffs and then promotes by setting `[router].profile_path`. Model calls and the Agent-SDK dependency live only in the offline verb.

**B. Online shadow calibration.** Wake the dormant `Calibrator`: `RoutingBackend.generate` hands every served exchange to an off-thread Agent-SDK judge that folds EWMA scores + staleness into the LIVE `ProfileStore` and snapshots to disk. Rejected as the core: the injected grader returns `Grade(decision=None)` and `policy.route` ignores `quality_score`, so the automatic path NEVER flips the deny gate — it cannot re-enable a proven-good local tier without B's own offline candidate + human promotion anyway; it only ever adds verify-caution via staleness. Worse, it samples EVERY served tier including the cloud/Claude tier and hands Claude output to a Claude judge (self-verification, constraint 1), and `record_grade` on a new cloud row `_seed_decision`-fail-closes to local/deny (profile_store.py:325-340), so it can silently deny the cloud fallback it just observed serving well. It also mutates the live store and edits the TTFT-sensitive streamed-allow path.

**C. Promote the schema key to `(tier_id, work_class, reasoning)` now.** Rejected as premature: it ripples through `_table` keying, the deny filter, `record_grade`, `_seed_decision`, `store_from_profile`, and every seed, and is only justified once the router selects reasoning PER-REQUEST against a single always-on serve.

## Decision

Adopt **Approach A** as the shippable write-back loop, offline-batch-first, with these binding choices:

1. **`run_live` generates through the REAL backend.** The batch builds each tier's actual `RelayBackend`/`CloudBackend` (which applies `body.update(Tier.extra_body)`), so the measured output is byte-identical to prod — fixing the thinking-ON drift by construction. The stale-model-id / `extra_body`-bypassing eval generator is replaced, not routed around.

2. **The independent judge is a concrete `AgentSDKGrader` calling Claude via the Claude Agent SDK.** It scores 0-1 over the committed 5-dimension DIMS /25 rubric and returns `Grade(score, decision=None)`. Independence is enforced STRUCTURALLY: the batch RAISES if asked to grade a tier whose model/family matches the judge. Because the cloud tier IS Claude, Claude-grading-Claude is REFUSED; P1-P5 grade LOCAL tiers only. Grading the cloud tier needs a genuinely independent non-Claude judge — FLAGGED as a human decision, because that reintroduces the raw-API/other-provider question the golden rules gate. If the Agent SDK cannot return deterministic structured scores, that is FLAGGED — never a fallback to the raw API.

3. **Writes carry an EXPLICIT decision + tier privacy + fingerprint.** The batch derives each decision via `decision_for_score` and passes it explicitly, so `record_grade`'s `_seed_decision` (which fail-closes a new row to local/deny) can never mislabel a measured cloud row. Each row is stamped with `serve_fingerprint(tier)`.

4. **Loading MERGES measured rows over the seed.** `store_from_profile` starts from `default_profile()` and overlays the doc's measured rows (or the doc must enumerate every seeded pair), so a partial candidate never silently re-verdicts unmeasured classes (today it would flip e.g. `(fast-local, long-context)` from seed `deny` to default `allow-with-verify`). This preserves the reviewable-A/B guarantee.

5. **Reasoning mode lives in the FINGERPRINT, not a new schema axis or tier id.** A deployed tier serves under one reasoning config; a thinking-on vs thinking-off serve is a different serve identity -> a different fingerprint. This requires ONE change: add `extra_body` to `fingerprint.IDENTITY_FIELDS` (it is not captured today) and bump `FINGERPRINT_SCHEMA`. The thinking-on/off A/B is then two serves = two fingerprints = two candidate profiles, diffed per work-class. The `(tier_id, work_class, reasoning)` schema-axis promotion is the explicit DEFERRED migration path, to be taken up (in a superseding ADR) only when the router selects reasoning per-request against a single always-on serve.

6. **Staleness goes live and gates trust.** `serve.build_server` calls `refresh_fingerprint(store, tier.id, tier)` per tier at startup before `RoutingBackend`; a serve whose identity drifted from the measured row is marked stale and `decision()` downgrades a stale `allow` -> `allow-with-verify`. A freshly-loaded profile adopts the baseline and does not spuriously stale.

7. **Promotion is human-gated.** The loop's output is a byte-diffable candidate `profile.json`; it gates traffic only when the operator diffs it against the incumbent and sets `[router].profile_path` (fail-fast load already exists). No automatic write changes routing.

Approach B is DEFERRED to a later additive phase (P6), permitted only after A ships and only with a HARD cloud-tier exclusion and every automatic write routed through the same reviewable candidate-file gate.

## Consequences

- **Kept:** stdlib-only hot path (the Agent-SDK judge and its dependency are lazily imported only by the offline `eval calibrate` verb); the load-bearing `deny` gate semantics; the frozen-dataclass profile style; fail-fast `profile_path` loading.
- **Changed:** `profile.json` schema -> v2 (per-row `fingerprint`/`stale`/`last_measured`/reasoning provenance); `store_from_profile` loads those fields and merges over seed; `fingerprint.IDENTITY_FIELDS` gains `extra_body` (schema bump); `build_server` stamps fingerprints at startup; `run_live` gains a real body; a new `grader_agentsdk.py` and `eval calibrate` verb; the drifting eval generator is replaced.
- **Given up / new constraints:** the candidate profile is NOT byte-stable across runs (an LLM judge at temp 0 still varies) — mitigated by median-of-k and by the fact the artifact is operator-reviewed, not CI-committed. The cloud tier stays on seed verdicts until an independent non-Claude judge is sanctioned by a human. A one-time re-stale occurs the first time an old profile is loaded after `extra_body` enters the fingerprint (documented).
- **Follow-up / downstream:** future per-request reasoning selection would promote the key to `(tier_id, work_class, reasoning)` via a superseding ADR; the deferred online-shadow layer (P6) would need its own ADR update if it ever mutates anything the operator has not reviewed. Any change to the `decision_for_score` thresholds gates traffic and needs human sign-off.
