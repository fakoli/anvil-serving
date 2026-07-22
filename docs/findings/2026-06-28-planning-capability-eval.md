---
title: "Planning-capability eval — local models vs frontier on anvil's real PRD→tasks prompt"
date: 2026-06-28
status: eval-result
question: "Is the local serving tier smart enough to do anvil's planning augmentation?"
models_under_test:
  - "HEAVY: qwen3-coder-local (cpatonn Qwen3-Coder-30B-A3B AWQ) — SGLang :30000"
  - "FAST: gpt-oss-20b (OpenAI MXFP4) — vLLM :30001"
baseline: "FRONTIER: Claude Opus 4.8 (the keyless agent-sdk default anvil ships with)"
data_dir: docs/findings/eval-data/2026-06-28-planning-capability/
source_revision: "fakoli/anvil-serving@31d95adaf68157b81318325356516cef9569b10f"
introduced_revision: "fakoli/anvil-serving@21f9a81f9be98dab3be15b07395ab34749d852b6"
notes_mirror_revision: "fakoli/anvil-serving-notes@7b46ceb6ae62252f8f808f6c065706a24e7970bb"
source_sha256: "ac812f3e107ab5efc20d37efc399cd1b6ed6512337f675dc8c27f6e634e6e653"
public_copy_date: 2026-07-22
---

# Planning-capability eval: is local smart enough for anvil's planner?

> **Publication and rerun status.** This is a sanitized public copy of the 2026-06-28 historical
> evaluation. Its complete 21-file, 159,272-byte canonical source bundle is published under
> [`eval-data/2026-06-28-planning-capability/`](eval-data/2026-06-28-planning-capability/PUBLICATION.md), with
> source hashes and publication notes in that directory. On 2026-07-22, the deterministic
> structural grader and aggregate calculator were rerun from the published inputs and reproduced
> the committed metrics with no diff. Model generation and blind judging were **not** rerun; the
> named models, endpoints, and frontier baseline remain historical observations, not current-model
> qualification.

> **One-line verdict.** On anvil's real PRD→tasks planning task, the local models land at
> **~55–65% of frontier quality** (judge totals: frontier **24.75/25**, fast **16.0**, heavy
> **13.25**). They reliably emit *parseable* anvil tasks, but **dependency-graph reasoning
> collapses** (local ≈2.0–2.25 / 5 vs frontier 5.0) and decomposition is unstable per-PRD.
> Both blind judges ranked frontier #1 on **both** PRDs, unanimously. For a task anvil already
> gets **free** from the Claude subscription, local is a clear downgrade here.

---

## 1. What was tested, exactly

anvil's **only** LLM call is planning augmentation: `generate_tasks_markdown` turns a PRD
(goals + requirements + features, no tasks yet) into a strict `## Tasks` markdown section
(`anvil/bin/src/anvil/planning/llm_planner.py`). We tested *that exact task*:

- **Prompt:** anvil's verbatim `_SYSTEM_PROMPT` (`llm_planner.py:395-504`) + a faithful copy of
  `_build_user_prompt` (`llm_planner.py:507-595`). One-shot, no tools, `temperature=0.0`,
  `max_tokens=8192` — matching `CustomEndpointProvider.generate` semantics
  (`planning/llm.py:993-1006`). The rendered prompts are preserved (`prompts/`).
- **Inputs (2 real anvil PRDs):**
  - **PRD-A** `anvil-backlog` — 8 features (F001–F008), 9 requirements (R001–R009), breadth-y,
    looser dependency structure.
  - **PRD-B** `multi-prd-revisable` (anvil v0.3) — 9 features / 8 phases (F001–F009),
    13 requirements, **infrastructure-heavy with hard phase ordering** (schema→migration→API→
    claim-gate→parser→CLI) and explicit ordering constraints (R007: "pin the cross-PRD moat
    regression tests BEFORE any `--prd` narrowing").
- **Models:** HEAVY `qwen3-coder-local` (:30000), FAST `gpt-oss-20b` (:30001) — both **live**,
  queried over the OpenAI-compatible API. **FRONTIER** baseline = Claude Opus 4.8 given the
  identical prompt (this is the model class anvil's default `agent-sdk` provider uses over the
  logged-in subscription — i.e. the realistic "do nothing" comparison).
- **Grading — two independent layers:**
  1. **Deterministic structural grader** (`grade_struct.py`) — scores against anvil's *own*
     parser/validator rules (`_validate_and_normalize`, `_TASK_HEADING_RE`): parseable, ID
     hygiene, field completeness, dependency integrity (dangling/self/cycle), feature coverage.
  2. **Blind judge panel** — 4 independent Opus judges (2 per PRD), each scoring 3 **anonymized**
     candidates (X/Y/Z, hidden mapping) 1–5 on five dimensions. Judges never saw which model
     produced which output.

**Design notes / bias controls.** Generation was deterministic (temp 0). Judges were blind and
independent; the frontier *producer* agent was a different instance from every *judge* agent
(no self-grading — consistent with the project's adversarial-gate principle). Candidate letters
used a different permutation per PRD to avoid positional bias.

---

## 2. Headline results

### 2.1 Overall quality (blind-judge total, averaged over 2 PRDs × 2 judges)

| Model | Decomp. granularity | Req. coverage | **Dependency correctness** | Acceptance verifiability | Faithfulness | **Total / 25** | % of frontier |
|---|---|---|---|---|---|---|---|
| **FRONTIER (Opus)** | 5.0 | 5.0 | **5.0** | 4.75 | 5.0 | **24.75** | 100% |
| **FAST (gpt-oss-20b)** | 4.25 | 2.75 | **2.0** | 3.5 | 3.5 | **16.0** | **64.6%** |
| **HEAVY (qwen3-coder-30b)** | 2.25 | 3.25 | **2.25** | 2.5 | 3.0 | **13.25** | **53.5%** |

**Dependency correctness is the universal local failure** — both local models score ~2/5 while
frontier scores a perfect 5/5. This is the single clearest quantitative signal in the eval.

### 2.2 Per-PRD breakdown (judge total /25, avg of 2 judges, with range)

| Model | PRD-A (backlog) | PRD-B (multi-prd) |
|---|---|---|
| FRONTIER | **25.0** (25–25) | **24.5** (24–25) |
| FAST | 17.0 (15–19) | 15.0 (14–16) |
| HEAVY | **10.0** (9–11) | 16.5 (16–17) |

**Failure modes are complementary and PRD-dependent**, not a uniform deficit:
- **HEAVY tanks on PRD-A (10/25)** by under-decomposing — 9 mega-tasks, ~one per feature.
- **FAST tanks on PRD-B (15/25)** by dropping two whole phases (F008, F009) and barely modeling
  dependencies. HEAVY actually did *better* than FAST on the infra-heavy PRD-B (16.5 vs 15).

### 2.3 Inter-judge agreement

Both judges produced **identical rankings on both PRDs** → high reliability:
- PRD-A: `frontier > fast > heavy` (both judges)
- PRD-B: `frontier > heavy > fast` (both judges)

---

## 3. Generation stats (live local inference)

| PRD | Model | Tasks | Completion tokens | Latency | Throughput | Output chars |
|---|---|---|---|---|---|---|
| A | HEAVY | 9 | 1,649 | 8.3 s | 198 tok/s | 7,510 |
| A | FAST | 21 | 5,333 | 20.7 s | 257 tok/s | 11,398 |
| B | HEAVY | 19 | 3,407 | 18.0 s | 189 tok/s | 12,977 |
| B | FAST | 15 | 3,881 | 15.2 s | 256 tok/s | 9,543 |
| A/B | FRONTIER | 22 / 18 | — (via agent) | — | — | — |

**Latency is a non-issue for planning.** At planning-sized context (~2–3k-token prompts), HEAVY
ran at **~190 tok/s with 8–18 s end-to-end** — nowhere near the 35 s TTFT feared in the handoff
(that figure was specifically 125k-context prefill; planning prompts have negligible prefill).
So the planning question is purely **quality**, not speed.

---

## 4. Structural grading (deterministic, anvil's own rules)

| PRD | Model | Tasks | Struct % | Seq IDs | Cycle | Dangling deps | **Dep edges** | Feature coverage |
|---|---|---|---|---|---|---|---|---|
| A | HEAVY | 9 | 100 | ✓ | none | 0 | **2** | 8/8 |
| A | FAST | 21 | 100 | ✓ | none | 0 | 13 | 8/8 |
| A | FRONTIER | 22 | 100 | ✓ | none | 0 | 5 | 8/8 |
| B | HEAVY | 19 | 100 | ✓ | none | 0 | 19 | 9/9 |
| B | FAST | 15 | **92** | ✓ | none | 0 | **3** | **7/9** (drops F008,F009) |
| B | FRONTIER | 18 | 100 | ✓ | none | 0 | **23** | 9/9 |

**Structural validity is NOT the differentiator** — every output parses cleanly under anvil's
rules (≥92%), no cycles, no dangling edges, full field completeness. Modern models reliably
follow the strict format. The signal hides in two columns:
- **Dep-edge count** tracks dependency *effort*: frontier 23 vs fast 3 on PRD-B is the
  quantitative shadow of fast failing to model phase ordering. (Edge *count* is necessary but
  not sufficient — fast's 13 edges on PRD-A were judged largely *spurious*; see §5.)
- **Feature coverage** caught fast silently dropping 2 of 9 phases on PRD-B.

---

## 5. Qualitative failure-mode analysis (de-anonymized, from judge notes)

### HEAVY (qwen3-coder-30b)
- **Under-decomposition (PRD-A):** 9 tasks, one mega-task per feature. `T008` packed all
  four–five R008 deliverables (evidence query, overlap surfacing, decision back-prop, atomic
  dep batching, contract gates) into one; `T009` packed Mermaid + GitHub projection together;
  R001's *standing concurrency regression suite* was bundled into the implementation task instead
  of being its own deliverable. Directly violates the prompt's "don't pack a whole feature into
  one task / ~10-20 tasks" sizing rule.
- **Dependency errors:** emitted **empty `**Dependencies:**` fields** (an explicit rule
  violation), and a **backwards edge** `T001→T002` (atomic claim-exclusion "depends on"
  fractional-lease support — it does not).
- **Hallucinated tooling:** a **`cargo test`** verification step in a Python/SQLite project.
- **Better on PRD-B:** full 9/9 coverage, 19 dep edges — but with **duplicate tasks**
  (`T008`/`T018` both implement event-sourced revision; `T010`/`T019` both finalize docs),
  filler tasks, and it **violated R007's "tests-before-narrowing" ordering**.

### FAST (gpt-oss-20b)
- **Good granularity, bad dependencies (PRD-A):** 21 well-sized tasks (correctly split R001 into
  impl + regression suite), but **invented spurious dependency chains** — e.g. a fully artificial
  `T016→T017→T018→T019` over *independent* R008 deliverables, and `T015→T014→T013` imposing
  ordering infrastructure doesn't demand. Also invented `--test`/`--test-overlap` flags and a
  **fabricated curl to `catalog.example.com`**.
- **Dropped scope (PRD-B):** silently **omitted Phase 7 (release/sync, F008/R012) and Phase 8
  (docs/version-lockstep, F009/R013) entirely**, plus R003's de-literalized migration ladder and
  R004's replay-equivalence oracle. Dep graph nearly empty (3 edges). A **heredoc `\n` rendering
  bug** left several verification one-liners non-runnable.

### FRONTIER (Opus) — reference
- Near-perfect both PRDs. Correctly split every multi-part requirement, emitted **only real,
  correctly-directed** dependency edges, and — critically — was the **only** candidate to encode
  R007's hard constraint (pin the cross-PRD moat regression tests, `T008`, *before* `--prd`
  narrowing, `T011`). Minor softness only in acceptance phrasing (AV 4.75).

**Cross-cutting insight:** the thing frontier did that *neither* local model did is **honor a
subtle, non-local ordering constraint stated in the requirements** (R007's moat-before-narrowing).
That kind of whole-PRD reasoning — not format, not coverage — is the real capability gap.

---

## 6. Threats to validity (read before over-generalizing)

1. **n = 2 PRDs.** Small sample; both are anvil's *own* (infra-dense, hard-end) PRDs. A typical
   user's starter PRD is simpler, where local would likely score higher. Treat the percentages as
   directional, not precise.
2. **Frontier baseline was produced by an Opus *agent*, not anvil's `agent-sdk` path.** Same model
   class, but harness/system-prompt differences exist. It's a faithful proxy for "what the
   subscription gives you," not a byte-identical reproduction.
3. **Judges are also Opus.** Same-family judge may modestly favor the frontier output's style.
   Mitigated by blind anonymization + identical cross-judge rankings, but not eliminated.
4. **Single decoding, temp 0.** No self-consistency / best-of-n. A local model with best-of-3 +
   a validator-retry loop would close part of the gap (especially the rule-violation errors like
   empty dependency fields, which are mechanically detectable and auto-rejectable).
5. **No human ground-truth.** "Correct decomposition" is judged, not measured against an
   authoritative task graph. The deterministic layer (§4) is the only fully objective signal.
6. **gpt-oss-20b reasoning tokens** are not separately accounted; `completion_tokens` is the
   server's count. Its higher token output partly reflects reasoning, not just task content.

---

## 7. Data artifacts (everything needed to re-analyze)

All under `docs/findings/eval-data/2026-06-28-planning-capability/`:

| Path | Contents |
|---|---|
| `prompts/prompt_prd*.txt` | The exact system+user prompts fed to every model |
| `outputs/out_<prd>__<model>.md` | All 6 raw task-graph generations |
| `grading/gen_manifest.json` | Generation stats (tokens, latency, tok/s) |
| `grading/grade_struct.json` | Full deterministic structural metrics (every check) |
| `grading/judge_prd{A,B}_{1,2}.json` | Raw per-judge scores + rationales (anonymized letters) |
| `grading/anon_map.json` | The hidden letter→model mapping (for de-anon) |
| `grading/output_sha256.json` | Binding from the six published outputs to the independent judge/aggregate record |
| `grading/metrics_long.csv` | **One row per PRD×model**: gen + structural + judge-avg (analysis-ready) |
| `grading/judge_dimensions_long.csv` | **Tidy/long**: prd × model × judge × dimension × score |
| `grading/aggregate.json` | Per-model overall, per-PRD×model, inter-judge agreement |
| `eval_gen.py`, `grade_struct.py`, `aggregate.py` | Historical local-generation and reproducible offline calculation scripts; frontier generation and blind-judge execution were separate and are not included |

**To reproduce the offline calculations:** run `python grade_struct.py` and then
`python aggregate.py`; a clean run reproduces the tracked JSON/CSV and refuses output bytes not
bound by `grading/output_sha256.json`. To repeat model generation,
first copy the evidence directory to a disposable location, verify that the historical model IDs
and loopback endpoints are intentionally available, set
`ANVIL_EVAL_CONFIRM_OVERWRITE=2026-06-28`, and run `python eval_gen.py`. That step overwrites
outputs in the copy and does not recreate the separately produced frontier baseline or four blind
judge records. Generation commits no file if any local request fails. After a successful new
generation, aggregation remains blocked until the new outputs have been independently judged and
the output binding is deliberately replaced. `metrics_long.csv` is the file to load into pandas/R
for independent cuts.

---

## 8. Interpretation for the anvil decision

This eval is about **Axis A** (anvil's own planning augmentation — the only thing
`.anvil/config.yaml` controls; see `2026-06-28-anvil-integration-audit.md`). Conclusions:

1. **Don't route anvil's planning to local.** It's a quality-sensitive, low-volume task that
   anvil already gets **free** from the Claude subscription. Trading 24.75→~14 quality to save
   $0 on a task you run occasionally is the worst trade in the system. The 35s-TTFT worry was a
   red herring (latency is fine); the *quality* gap is real and is exactly in the dimension that
   matters for planning — dependency/ordering reasoning.
2. **If you ever must run planning locally** (air-gapped, no subscription), prefer **HEAVY for
   infra-heavy PRDs** and add a **validator-retry loop** (anvil's own `_validate_and_normalize`
   already rejects empty-dependency / unparseable output; auto-retry would kill the cheap
   rule-violation errors). Best-of-3 + the deterministic gate would likely lift local from ~60%
   toward ~75%.
3. **This says nothing about Axis B** (the coding fleet). Planning is the *hardest* reasoning anvil
   does; the fleet's high-volume mechanical work (state updates, evidence validation, simple edits)
   is a much easier bar where local FAST is plausibly sufficient. A separate eval on real fleet
   traffic is the next measurement — and it's the one that actually moves cost.
