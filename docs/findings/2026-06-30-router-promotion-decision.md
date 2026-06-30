# Per-work-class promotion decision — which work runs LOCAL vs CLOUD-default (T018)

> **Status:** decision (M3, reframed bake-off T015 decide-gate). rev 2026-06-30.
> **Requirement:** R010 / F008 / T018 — *"a work-class is routed local only if it clears its measured
> quality bar **and** relieves measured capacity pressure; planning/critic are never auto-promoted on
> quality grounds."* ([`prd:105-107`](../prd/anvil-serving-harness-router.prd.md),
> [`prd:495-511`](../prd/anvil-serving-harness-router.prd.md))
> **Grounds (cited, not invented):**
> [`findings/2026-06-28-planning-capability-eval.md`](2026-06-28-planning-capability-eval.md) (the one
> HARD per-work-class measurement), the T015 bootstrap replay of the committed eval
> ([`router/profile_bootstrap.py`](../../anvil_serving/router/profile_bootstrap.py)), the T005 seed
> verdicts ([`router/profile_store.py:56-63`](../../anvil_serving/router/profile_store.py)), the
> canonical taxonomy ([`router/classify.py:34-41`](../../anvil_serving/router/classify.py)), and the
> product principle ([`QUALITY-GATED-ROUTER.md`](../QUALITY-GATED-ROUTER.md),
> [`DIRECTION.md:340-352`](../DIRECTION.md)).

---

## 1. The promotion principle (and the two classes it does not apply to)

The router's job is **"local where it's been proven, cloud where it hasn't — verified, with automatic
fallback"** ([`DIRECTION.md:18-21`](../DIRECTION.md)). Promotion is therefore a **two-gate AND**, per
R010:

1. **Quality gate** — the work-class clears its measured quality bar on a local tier, *and*
2. **Pressure gate** — promoting it relieves a *measured* throughput/capacity pressure (otherwise
   there is no win to bank — see the 529-overload evidence in
   [`2026-06-27-capacity-throughput.md:9-26`](2026-06-27-capacity-throughput.md)).

A class is **promoted to local only if it clears BOTH**. Failing either keeps it cloud-default.

**Two classes are exempt from this calculus on PRINCIPLE — they are cloud-default (failover-only) and
are NOT subject to a throughput-pressure override:**

- **`planning`** — the one class we measured directly. Local collapses on **dependency/ordering
  reasoning** (frontier 5.0/5 vs local ~2.0–2.25/5), the exact capability planning needs
  ([`planning-capability-eval.md:64-72`](2026-06-28-planning-capability-eval.md)). It is also
  **low-volume** (no pressure to relieve) and **free on the subscription** — so trading 24.75→~14
  quality to save \$0 is "the worst trade in the system"
  ([`planning-capability-eval.md:217-225`](2026-06-28-planning-capability-eval.md)).
- **`critic`** (the adversarial gating reviewer — anvil's PASS / MUST-FIX merge gate; on the wire it
  classifies as the `review` work-class) — a critic verdict **gates a merge**, so a wrong silent
  "PASS" ships a bug, the precise silent-failure the product exists to prevent
  ([`QUALITY-GATED-ROUTER.md:298-304`](../QUALITY-GATED-ROUTER.md)). It is the *same* whole-context
  dependency-reasoning judgment the planning eval showed local failing
  ([`planning-capability-eval.md:160-168`](2026-06-28-planning-capability-eval.md)), so it inherits
  the planning verdict by principle even though it has no eval of its own.

These two are documented as **cloud-default (failover-only) regardless of throughput pressure** — no
amount of measured capacity pressure auto-promotes them ([`DIRECTION.md:349`](../DIRECTION.md)).

**Verdict vocabulary** (maps onto the store's closed decision set
[`profile_store.py:31`](../../anvil_serving/router/profile_store.py)):

| Verdict | Meaning | Store decision |
|---|---|---|
| **run-local** | local is the default tier for the class; cheap structural verify (T007) still applies | `allow` |
| **allow-with-verify** | local may serve but **every** response is verify-gated with cloud fallback; a *capacity-relief* lever, never a quality promotion | `allow-with-verify` |
| **cloud-default (failover-only)** | cloud is the default; local serves **only** when cloud is unavailable, and still verify-gated | `deny` (local dropped from the normal pool) |

---

## 2. Per-work-class decision table

Covers **every** entry of `WORK_CLASSES`
(`chat, bounded-edit, multi-file-refactor, planning, review, long-context`
— [`classify.py:34-41`](../../anvil_serving/router/classify.py)). "Measurement" column states whether
the verdict rests on **HARD** data (the planning eval) or a **SEED** verdict awaiting its own eval.

| Work-class | Measured quality (source) | Throughput pressure | **Verdict (fast-local / heavy-local / cloud)** | Rationale |
|---|---|---|---|---|
| **planning** | **HARD.** frontier **24.75/25 (0.99)**, fast **16.0/25 (0.64)**, heavy **13.25/25 (0.53)**; dependency-correctness frontier 5.0 vs local 2.0–2.25 ([`eval:64-72`](2026-06-28-planning-capability-eval.md)) | Low (low-volume, free on sub) | **cloud-default (failover-only) / failover-only / run-local** | Local fails the quality gate outright on dependency reasoning; low volume means no pressure to relieve either. **Never auto-promoted regardless of throughput pressure.** Bootstrap confirms: `fast-local deny, heavy-local deny, cloud allow`. |
| **review** *(broad feedback)* | **SEED** (no eval). Anchors: fast 0.55 `allow-with-verify`, heavy 0.80 `allow` ([`profile_store.py:60`](../../anvil_serving/router/profile_store.py)) | Medium — review subagents are where live 529 overloads struck ([`capacity:14-21`](2026-06-27-capacity-throughput.md)) | **allow-with-verify / allow-with-verify / run-local** | Non-gating "give me feedback" is a softer bar; seed allows local *with verify*. **UNMEASURED** — promotion is provisional pending a review-class eval. **See the critic carve-out below — gating critic is NOT this.** |
| **critic** *(gating review — the adversarial merge gate; classifies as `review`)* | **PRINCIPLE** (inherits planning's dependency-reasoning result; no direct eval) | n/a (exempt) | **cloud-default (failover-only) / failover-only / run-local** | A wrong silent PASS ships a bug — the catastrophic silent-failure. Same whole-context dependency judgment local failed on planning. **Cloud-default (failover-only) regardless of throughput pressure.** |
| **multi-file-refactor** | **SEED + extrapolation.** Anchors: fast `deny`, heavy 0.70 `allow-with-verify`, cloud `allow` ([`profile_store.py:58`](../../anvil_serving/router/profile_store.py)); flagged `HIGH_RISK_LOCAL` ([`profile_store.py:46`](../../anvil_serving/router/profile_store.py)) | Medium | **cloud-default (failover-only) / allow-with-verify (capacity-relief only) / run-local** | Cross-file ordering is the **same dependency reasoning** planning measured local failing (2/5). **Cloud-default (failover-only) regardless of throughput pressure**; the heavy-local `allow-with-verify` is a single verify-gated capacity-relief escape hatch, **not** a quality promotion. fast-local already `deny`. |
| **bounded-edit** | **SEED** (no eval). Anchors: fast 0.65 `allow`, heavy 0.80 `allow` ([`profile_store.py:61`](../../anvil_serving/router/profile_store.py)) | **High** — the high-volume mechanical work (state updates, evidence validation, simple edits) that dominates fleet decode ([`eval:231-234`](2026-06-28-planning-capability-eval.md)) | **run-local / run-local / run-local** | The strongest promotion candidate: clears the *pressure* gate hard, and the eval explicitly calls this "a much easier bar where local FAST is plausibly sufficient." Promoted on the seed, **pending its own eval** to confirm the quality gate; cheap structural verify (diff-applies / parses) backstops it. |
| **chat** | **SEED** (no eval). Anchors: fast 0.65 `allow`, heavy 0.80 `allow` ([`profile_store.py:62`](../../anvil_serving/router/profile_store.py)) | High (high-volume, low-stakes) | **run-local / run-local / run-local** | Low-stakes Q&A with no dependency-graph or merge-gating risk; high volume → real relief. Promoted on the seed, **pending its own eval**. |
| **long-context** | **SEED, constraint-grounded.** fast `deny`, heavy 0.80 `allow`, cloud `allow` ([`profile_store.py:59`](../../anvil_serving/router/profile_store.py)) | Medium | **cloud-default (HARD constraint) / run-local / run-local** | fast-local `deny` is a **hard window constraint, not a quality verdict** — its 32k ctx can't hold the request ([`benchmark-capacity`](2026-06-28-benchmark-capacity.md)); heavy-local has the window, so it runs local. A fit/capacity decision, not a reasoning one. |

> The three `planning` rows reproduce **exactly** what the T015 bootstrap emits from the committed eval
> (`python -m anvil_serving.router.profile_bootstrap --replay docs/findings/eval-data/ --out profile.json`):
> ```
> tier         work_class  model     score  decision  n  avg/25
> cloud        planning    frontier  0.99   allow      4   24.75
> fast-local   planning    fast      0.64   deny       4   16.0
> heavy-local  planning    heavy     0.53   deny       4   13.25
> ```
> The thresholds (`allow >= 0.85`, `deny < 0.70` — [`profile_bootstrap.py:104-105`](../../anvil_serving/router/profile_bootstrap.py))
> were chosen to **reproduce** the T005 seed, so the data-grounded seed and the hand-authored seed agree
> on the only class that has been measured.

---

## 3. The two required explicit statements

- **`planning` → cloud-default (failover-only), regardless of throughput pressure.** Grounded in the
  HARD measurement: local scores ~2/5 on dependency correctness vs frontier 5/5
  ([`eval:64-72`](2026-06-28-planning-capability-eval.md)); it is low-volume and free on the
  subscription. Never auto-promoted on quality grounds; not subject to a pressure override.
- **`critic` / `multi-file-refactor` → cloud-default (failover-only), regardless of throughput
  pressure.** Both are **whole-context dependency-reasoning** work — the exact capability the planning
  eval showed local failing (the non-local ordering constraint *neither* local model honored,
  [`eval:160-168`](2026-06-28-planning-capability-eval.md)). A critic's wrong silent PASS gates a bad
  merge; a refactor's missed cross-file edge corrupts a long run. Local is a **failover-only**
  participant (verify-gated) — never auto-promoted on quality grounds even under capacity pressure.

---

## 4. Measurement status — what rests on HARD data vs SEED (be honest)

| Class | Basis | Confidence |
|---|---|---|
| **planning** | **HARD** — blind 4-judge eval on anvil's real PRD→tasks prompt, 2 PRDs, temp 0; reproduced by the T015 bootstrap | High (the one directly-measured verdict) |
| **critic** | **PRINCIPLE** — inherits planning's dependency-reasoning result by analogy (no direct eval) | Medium — by-design conservative (fail-closed) |
| **multi-file-refactor** | **SEED + extrapolation** from planning's dependency-correctness column; `HIGH_RISK_LOCAL` fail-closed | Medium |
| **review / bounded-edit / chat / long-context** | **SEED** hand-authored anchors ([`profile_store.py:56-63`](../../anvil_serving/router/profile_store.py)); `long-context` fast-deny is a measured **window constraint**, not a quality verdict | Low–medium — provisional, awaiting each class's own eval |

**The loop that closes this:** these seed/expected verdicts are *not* the final word. **T016**
async calibration ([`router/calibrate.py`](../../anvil_serving/router/calibrate.py)) samples real
routed responses, grades them with cloud off the hot path, and folds the result into the profile
(`record_grade`), updating `quality_score`/`sample_n` and re-deciding each row on **live** numbers.
**T017** metrics ([`router/metrics.py`](../../anvil_serving/router/metrics.py)) then reports, per
work-class, the **accept-rate / silent-failure rate / cloud-tokens-saved** that actually adjudicate a
promotion. **Honest caveat:** T017's committed `traffic.jsonl` is a **synthetic** fixture (a
deterministic replay so CI can pin the silent-failure gate with no network/clock/live tier —
[`metrics.py:21-27`](../../anvil_serving/router/metrics.py)); the *real* per-class accept and
silent-failure rates come from a **live** capture window, not the fixture. So today only **planning**
carries a hard quality verdict; every other promotion above is a seed/expected call that the
T016→T017 loop must confirm on real traffic before it is more than provisional.

## 5. Places I extrapolated beyond the eval (flagged)

1. **`critic` is not in `WORK_CLASSES`.** It is anvil's adversarial-reviewer *agent role*; on the wire
   it classifies as `review`. Its cloud-default verdict is by **principle** (planning's dependency
   result + the merge-gating stakes), not a critic-specific measurement.
2. **The `review` seed (`allow-with-verify`/`allow`) is more permissive than the critic carve-out.**
   This is deliberate, and is the one place the broad seed and this decision diverge: the seed stands
   for *non-gating* feedback; the moment a review **gates a merge** (critic), it is cloud-default. A
   review-class eval is needed to separate these empirically.
3. **`multi-file-refactor` and `long-context` have no eval of their own.** The refactor verdict is
   extrapolated from planning's dependency-correctness signal; the long-context fast-deny rests on the
   measured 32k window cap, not a graded quality run. Both should get their own eval before the
   heavy-local lanes are treated as more than capacity-relief.
