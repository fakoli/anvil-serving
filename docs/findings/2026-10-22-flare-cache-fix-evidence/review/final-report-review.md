# FLARE-FIX-001 final report independent review

```json
{
  "schema_version": "operator-workflow/v1",
  "request": "Independent consistency review of the closed FLARE-FIX-001 summary, report, README, coverage record, restoration record, and publication narrative",
  "gate_state": "hold_for_two_targeted_corrections",
  "targets": [
    "summary.json",
    "report.md",
    "README.md",
    "coverage-and-gaps.md",
    "restoration.json",
    "publication/narrative-draft.md"
  ],
  "tools_used": [
    "read-only file inspection",
    "independent JSON structure and denominator checks",
    "independent SHA-256 reference verification"
  ],
  "artifacts": {
    "findings_summary": {
      "critical": 0,
      "high": 0,
      "medium": 1,
      "low": 1
    },
    "reviewer_independence": "Implementer: GPT-6 Astra. Reviewer: GPT-5.6 Sol. Evaluated model/runtime: GLM-5.3-Flash under pinned Flare. The reviewer did not generate the model responses or author the reviewed report.",
    "verified_counts": {
      "summary_evidence_hashes": "9/9 match",
      "restoration_source_hashes": "34/34 match",
      "restoration_windows": 9,
      "lane_repetition_rows": 27,
      "unique_lanes": 9,
      "repetitions_per_lane": 3,
      "performance_eligible_true": 0,
      "formal_requests": 2550,
      "prime_requests": 1152,
      "measured_requests": 1398,
      "warmups": 162,
      "fresh_formal_incarnations": 54
    }
  },
  "advisory_priors": [
    "Natural-output equality is unverified because token IDs are absent; it is not proven different.",
    "The r1-B monitoring/runner-exit limitation and r2-B false original restoration proof remain historical exceptions.",
    "Diagnostic causal support applies only to fixed-work and does not clear any qualification, parity, default, or promotion gate."
  ],
  "recommendation": "HOLD public packaging until the two summary.json consistency corrections below are made. No evidence rerun or gate rewrite is required.",
  "human_gate_required": false,
  "promoted": false
}
```

## Findings

### Medium — protected-regression signs are ambiguous in the machine-readable summary

**Confirmed.** `summary.json:114-117` calls the protected results regressions but renders them as `-8.197%` and `-13.105%`. Those values follow the descriptive summary's “positive favors B” convention, but `summary.json` does not state that convention. The independently accepted matrix review instead reports the same effects as positive latency increases (`review/final-matrix-review.md:27-36`). A consumer reading only the decision summary can therefore interpret the sign in the opposite direction.

Use direction-explicit wording such as “B latency was 8.197% higher” and “B TTFT was 13.105% higher,” or add a machine-readable delta convention and keep it consistent. Do not change the magnitudes, the underlying signed fields, or the false qualification gates.

### Low — causal-gate reason is stale after accepted independent review

**Confirmed.** `summary.json:99-102` says the manual fixed-work assessment is “under independent review,” while the same file binds the accepted independent review at `summary.json:70-74` and says it was accepted at `summary.json:601-602`. Update only the reason to distinguish the still-false original causal proof gate from the accepted diagnostic-only manual assessment. The gate must remain `passed=false`.

## Verified report boundaries

- `summary.json` has schema `anvil-serving.benchmark-decision-summary/v1`; `restoration.json` has schema `anvil-serving.benchmark-restoration/v1`. All nine summary evidence references and all 34 restoration source references exist and hash correctly.
- The summary contains exactly 27 unique lane/repetition rows: all nine lanes across repetitions 1-3. Every row has `performance_eligible=false`. The README presents all 27 rows and defines its displayed delta as positive when it favors B (`README.md:37-42`).
- The whole-campaign denominators agree across the reviewed files: 2,550 formal requests = 1,152 prime + 1,398 measured, plus 162 warmups across 54 fresh formal incarnations. The failed 8.94875-minute r2-B pretraffic window sent zero formal traffic and remains outside that denominator (`report.md:36-41`; `restoration.json:198-220`).
- Natural-output rows are consistently described as `unverified_ids_absent`, never as proven unequal (`README.md:30-35,46-51,70-72`; `publication/narrative-draft.md:51-57`). Decode C4 alone is described as unmatched output/work.
- Restoration is accurately scoped as historical and verified with retained exceptions. r1-B's unknown outer-runner exit and monitoring limitation remain visible; r2-B's original restoration proof stays false and its later matching-generation receipt remains supplemental (`restoration.json:68-92,120-145,223-236`).
- The public narrative explicitly retains the failed exact-128 numerical gate, all 27 incomplete strict comparisons, no qualification/default/promotion/submission, protected regressions, C4 mismatch, and historical restoration limits.

## Breakage probes

| Axis | Concrete input or state | Result |
| --- | --- | --- |
| Unreadable or drifted evidence | Missing file or changed byte content for any summary/restoration reference. | **Refuted for retained bytes:** 9/9 summary and 34/34 restoration references match. |
| Malformed or boundary interpretation | Read the signed protected deltas without the README's convention. | **Confirmed:** the standalone summary permits the wrong directional interpretation; medium finding above. |
| Resource or denominator exhaustion | Omit a lane/repetition, warmup population, failed zero-traffic window, or restoration window. | **Refuted:** 9 lanes x 3 repetitions, 2,550 formal requests, 162 warmups, 54 incarnations, and 9 restoration windows reconcile. |
| State drift across reviews and closeout | Keep pre-review wording after binding the accepted review, or rewrite the r2-B false proof using later evidence. | **Confirmed** for the stale causal-gate phrase; **refuted** for r2-B because the supplement remains explicitly non-backdated. |

Residual risk after these text-only corrections remains the accepted campaign limitation: exact-128 numerical fidelity, independent component/state oracles, protected-lane guardrails, C4 matching, and general performance qualification all remain false or unavailable. No human promotion gate has been exercised.

## Closure addendum — final GO

The two requested corrections are complete:

- `summary.json:99-102` now states that the initial proof receipts remain false while the independently reviewed manual fixed-work assessment is accepted only as diagnostic causal support. The causal gate remains `passed=false`.
- `summary.json:114-117` now describes the protected regressions directionally: B small12 E2E latency was 8.197% higher and B pageheavy TTFT was 13.105% higher.

Substituting only those two corrected strings with their prior values reconstructs the previously reviewed `summary.json` SHA-256 exactly (`586e833d796d760719068a7970edd8e26884fcce2b30607a70cf58478497d56f`). This proves the delta is limited to the two approved text corrections. The corrected summary SHA-256 is `3b3ba91ea958e43a04a62695aff5ee19fab88c5f56736d37fe70d6c5fbe3e84b`; all 27 lane/repetition rows remain present, all remain `performance_eligible=false`, and no numeric value or gate boolean changed.

**Final recommendation: GO for report/publication packaging within the reviewed diagnostic-only scope.** The prior findings remain retained above as review history and are closed by this addendum. Qualification, default change, upstream submission, and promotion remain rejected; `promoted=false`.
