# FLARE-FIX-001 final causal review

```json
{
  "schema_version": "operator-workflow/v1",
  "request": "Independent final causal review of the closed six-arm FLARE-FIX-001 campaign",
  "gate_state": "accept_diagnostic_only",
  "targets": [
    "analysis/cache-causal-observations.json",
    "analysis/final-causal-assessment.json",
    "analysis/final-causal-assessment.md"
  ],
  "tools_used": [
    "read-only source and evidence inspection",
    "independent SHA-256 ledger verification",
    "offline native-attempt recomputation",
    "cache-causal-extraction.py --self-check"
  ],
  "artifacts": {
    "findings_summary": {
      "critical": 0,
      "high": 0,
      "medium": 0,
      "low": 1
    },
    "reviewer_independence": "Implementer: GPT-6 Astra. Reviewer: GPT-5.6 Sol. Evaluated model/runtime: GLM-5.3-Flash under pinned Flare. The reviewer neither generated the model responses nor authored the causal assessment.",
    "verified_population": {
      "extractor_inputs": 1182,
      "extractor_input_hash_mismatches": 0,
      "fixed_work_measured_requests": 312,
      "arms": 6,
      "complete_matched_requests_per_pair": 52,
      "pair_order": ["AB", "BA", "AB"]
    },
    "causal_scope": "Exact serial one-token reverse-replay workload only"
  },
  "advisory_priors": [
    "The failed numerical gate, original false receipts, protected regressions, natural-output ID absence, unmatched C4 work, and provenance exceptions remain binding limits.",
    "Diagnostic causal support is an explanatory closure label, not qualification, parity, a production recommendation, or promotion evidence."
  ],
  "recommendation": "Accept the manual diagnostic-causal-support assessment for the exact fixed-work workload. Reject candidate recommendation, allocator policy adoption, incumbent parity, general speedup, qualification, or promotion claims.",
  "human_gate_required": false,
  "promoted": false
}
```

## Findings

### Low — extractor integrity checks depend on Python assertions

**Confirmed.** `analysis/cache-causal-extraction.py:117,131,139,142,174` uses `assert` for the frozen corpus hash, proof pointers, registry hashes, generation binding, and prime order. Running the extractor with `python -O` would remove those checks and could publish observations from drifted inputs. The same file also reads its closed evidence without an explicit size or symlink bound (`:70-80`).

This does **not** invalidate the retained assessment: the recorded extractor source does not claim an optimized invocation, this review independently rehashed all 1,182 unique ledger entries with zero mismatches, the retained corpus totals about 86.0 MB with a 1,661,999-byte largest input, and the output is immutable by exclusive-create behavior (`:222-225`). The self-check passed under the normal interpreter. Before reusing this extractor as a general gate, replace the integrity assertions with explicit exceptions and add bounded regular-file reads. No gate or result needs rewriting for this closed campaign.

No material blocker was found.

## Independent evidence checks

- All 1,182 `source_artifacts` paths are unique and their current SHA-256 values match `analysis/cache-causal-observations.json`. All 19 references in `analysis/final-causal-assessment.json` also match.
- Every arm retains exactly 52 measured cases in reverse order, for 312 total request checks. Each row has the full admission, cost, capacity, completion, fetch/store failure, cancellation/failure, idle/drain, retained-delta, output, and prompt-accounting predicates from `analysis/cache-causal-extraction.py:40-67`.
- Eligible completed restores independently resolve to A=10 and B=36 in each repetition. Non-restoring requests remain in the denominator. The common noneligible cases are explicitly categorized; they were not dropped to manufacture a survivor population.
- Each arm's range manifest generation equals its retained registration generation. All seven native pool/pressure series have one exact-generation series and no range issue. Endpoints reproduce A rows/pages 52/7,314 with row-pressure and eviction delta 112, versus B 158/2,434 with both deltas zero; page-pressure delta is zero in all six arms.
- Direct native recomputation confirms 52/52 matched bodies, prompt counts, one-token IDs, reasoning state, and speculative-work vectors in each pair. Median client-generated TTFT and reductions reproduce 469.982/18.494 ms (96.065%), 464.987/23.075 ms (95.038%), and 467.585/20.236 ms (95.672%). These are complete-population first-generated-token observations, not visible-answer, decode, or general throughput claims.
- The counter interpretation is consistent with `review/cache-counter-semantics.md:20-51,56-92`: admission extension alone is insufficient, while the accepted rows additionally require one completed physical fetch, no failures/cancellation, idle transfer state, completed request, positive host extension, and exact prompt segmentation.

## Claim boundaries

The label **diagnostic causal support** is appropriately bounded by the closure procedure at `analysis/final-causal-procedure.md:26-36` and the final prose at `analysis/final-causal-assessment.md:3-21`:

- The strict numerical gate remains failed. One-token equality cannot establish 128-token continuation fidelity.
- Repetition 3 small12 E2E regressed 8.197% and pageheavy TTFT regressed 13.105%, both beyond the frozen 3% protected limit. Their causes are unknown, and they block a qualified recommendation.
- Natural-output rows lack retained token IDs, so equality is unverified. C4 decode has unmatched output IDs and whole-batch speculative work. Neither population supports parity or an allocator performance conclusion.
- r1-B retains arm-wide monitoring gaps even though the fixed-work lane has exact-generation monitoring evidence. r2-B's original restoration proof remains false; the later matching registration is supplemental and is not backdated. These exceptions prevent strict qualification and are stated explicitly.
- `performance_eligible=false`, `recommend_candidate=false`, and `promoted=false` remain unchanged. No global default, upstream submission, policy change, or incumbent parity follows.

## Breakage probes

| Axis | Concrete attack | Result |
| --- | --- | --- |
| Unreadable or drifted evidence | Remove/change a ledger input or alter a proof/registry hash. | **Refuted for retained bytes:** 1,182/1,182 hashes and 19/19 assessment references match. **Plausible under future `python -O`:** see the low finding. |
| Malformed/boundary input | Duplicate/nonfinite Prometheus scalars, wrong generation, rejection/fetch failure, non-idle transfer, bad output/accounting, CRLF, or path metacharacters. | **Refuted for this corpus:** parser splitting tolerates CRLF; no shell interpolation is used; self-check rejects the metric/state corruptions. General path custody is not hardened. |
| Resource exhaustion | Oversized evidence file or unbounded source tree. | **Refuted for this closed corpus:** 86.0 MB total and 1.66 MB maximum were read successfully. **Plausible for reuse:** reads have no explicit size bound. |
| State drift across arms/processes/config | Wrong registration generation, missing range, changed frozen body, order drift, or incomplete 52-request population. | **Refuted:** six exact generations, zero range issues, AB/BA/AB order, and 52/52 complete matched requests per pair were independently verified. |

Residual risk is limited to the deliberately unqualified areas above and the extractor-hardening issue. It does not change the accepted diagnostic-only interpretation.
