# Descriptive analysis review

One bounded offline pass of `analysis/describe.py` and `analysis/check-describe.py`. No live evidence files were opened, no hardware/HTTP/builds or heavy tests were used, and no analysis/harness code was modified. Existing synthetic check passes. Decision: **focused corrections required before reporting its work-match totals.** No strict-gate relaxation or framework expansion is needed.

## Findings

1. **Unknown decode batch work can coexist with a reported complete work match.** `match_case` validates decode rows without a per-request speculative vector, correctly reflecting batch ownership. But `pair_summary` totals `output_work_matches` before considering the batch vector. A valid one-row decode pair with batch_a=None and batch_b=None returns output_work_matches=1, output_work_mismatches=0, while its separate batch object says A_valid=false/B_valid=false/equal=false. Unequal valid batches have the same misleading match-total problem. This violates the requirement not to treat mutually missing work as matched. Also, a row with ok=false but otherwise complete fields currently returns output_work_match=true.

   Minimal correction: distinguish per-request observed output equality from complete work equality, or make the existing full-work match result require successful rows and, for decode, one valid equal whole-batch vector. Preserve the batch vector once; do not copy it to each request. Keep explicit missing/invalid/mismatched reasons. Descriptive percent deltas may still be computed from complete successful metric populations when work differs, provided they remain clearly descriptive and never imply matched-work qualification.

2. **Input normalization failures are omitted.** `describe` copies attempts, batch work and lane metadata, but neither root-level nor per-run normalization_issues/receipt failures are retained. A synthetic input with root normalization_issues and per-run containment failures yields an output containing none of those facts; complete attempt rows can still show an observed delta. Real wrapper/identity/phase failures can occur after valid responses, so attempt.ok alone is not sufficient to represent the normalized run's failures.

   Minimal correction: retain normalized root and per-run issue lists and relevant receipt/error status beside the existing request denominators, without changing their meaning or upgrading false receipts. Surface duplicate/missing run selection explicitly as well as run_count. A descriptive delta need not become a qualification gate, but its associated run failures must remain visible.

## Checks and sound behavior

- `python3 check-describe.py` passes its current synthetic cases.
- Independent tiny synthetic probes reproduced both findings: absent decode batches produced one match; a failed complete decode row matched itself; root/per-run normalization failures disappeared.
- Observed percent delta correctly requires identical complete measured case populations, unique identities, finite positive metrics and no failed measured attempts. It does not select only matched survivors. Keep this population rule.
- Candidate recommendation/performance eligibility are hardcoded false and numerical_gate remains failed. Strict analyzer code is imported for validation, not modified.
- Decode speculative vectors are retained once at batch level. Raw GPU-reused and host-reused counters remain separate; no general hit-rate interpretation is fabricated.
- Phase counter sums retain contributing_count and expected_count; missing values are not converted to zero.
- Failed/missing request denominators are retained. Per-arm metric summaries include every finite positive metric even on a failed attempt; either label that population explicitly or distinguish completed-request metrics so readers cannot mistake incomplete-response elapsed time for successful latency. This last point is a presentation clarification, not an additional acceptance framework.

Add only focused synthetic cases for absent/unequal decode batches, failed rows with otherwise complete fields, and preserved normalization failures. No rerun of model traffic or rewrite of strict acceptance gates is warranted.

## Final bounded repair check

Rechecked only the two findings using `python3 check-describe.py` and independent tiny synthetic inputs. No live evidence reads or broader review cycle. The updated synthetic suite passes.

- **Accepted:** aggregate complete_work_matches is now0 for both absent and unequal decode batch vectors, with the vector retained once. Failed rows with otherwise complete fields are rejected as complete work matches. Output equality is separated from full-work aggregate counts.
- **Accepted:** root normalization issues and per-run issues, false receipts and phase-summary errors are retained unchanged. Metric summaries explicitly label their population as all measured rows including failures. Candidate recommendation remains false.
- **Residual concrete schema issue:** nested `pair.cases[*].complete_work_match` is calculated before the batch test and remains true for identical valid decode rows even when both batch vectors are absent or unequal. The same pair correctly reports complete_work_matches=0 and batch_speculative_work.equal=false. Independent probes reproduced both contradictions. This is the remaining part of the original decode matching finding, not a new review category.

**Reporting decision:** GO for descriptive metrics, denominators, retained failures and corrected aggregate match totals. Omit nested decode `complete_work_match` from conclusions/tables, or explicitly label it as per-request field agreement that excludes batch work. Do not present it as complete decode-work matching. The parent has capped repair turns, so no further author change is requested. Unqualified use of that nested flag remains blocked; the current strict/numerical gates remain untouched.
