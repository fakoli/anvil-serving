# Final diagnostic matrix independent interpretation review

**Accept the complete matrix as descriptive diagnostic evidence; reject qualification, promotion, parity and causal-speedup interpretations.** All27 strict lane/repetition comparisons remain incomplete. No analysis or acceptance gate was changed. No material arithmetic discrepancy was found in the focused independent recomputation.

## Provenance and denominators

Independently verified the three source and output SHA-256 pairs recorded in analysis/final-analysis-execution.json. Frozen normalizer output hash is `5d47ecba13a6b7cc089ebe7fcb07018bcf94cc872e38190c30d1896bb6209143`; strict analysis `0ca23252e62b7bfeb2b75c6679dcae201fde494f9574f020d56d48b0d0597c76`; descriptive output `2a7d15b0443c6f5e3124a10d46c2ff0ccaf3faea5fedafa1dcb383707a70eca9`. Execution sources match those recorded hashes.

The normalized matrix contains54unique lane/repetition/arm records. Independent native phase-summary totals and normalized attempts agree: **2550formal requests =1152priming+1398measured, plus162separate warmups**. There are zero reported formal request errors, zero missing targets, zero uncertain target slots, and attempted_count_is_lower_bound=false. Actual normalized order is AB/BA/AB. This does not erase the separate failed pre-traffic r2-B window, its outage/restoration, r1-B's unavailable outer runner exit, or monitoring/evidence failures. “Zero formal request errors” must not become “no campaign failures.”

## Fixed-work observations independently recomputed

Read all six native fixed-work measured attempt arrays directly and compared them with normalized rows. Every arm/repetition contains exactly52successful measured cases; paired case sets are identical with no duplicates or omissions. Each case has the same frozen request-body hash, prompt count, nonempty one-token output ID, and exact speculative-work vector across A/B. Body hashes were independently recomputed from the frozen52body file. Each row reports usage completion_tokens=1; the full four speculative metric families have identical label support, nonnegative integer counts, consistent offered/accepted totals and accepted<=proposed. All52cases match in each repetition; no survivor filtering was used. Missing reasoning counts remain null under the prospectively accepted fixed1 exception.

Client-generated TTFT medians, recomputed from native attempt values:

| Repetition | A25% (ms) | B75% (ms) | Observed reduction | Matched cases |
| --- | ---: | ---: | ---: | ---: |
| 1 | 469.982182 | 18.494285 | 96.064897% | 52/52 |
| 2 | 464.986594 | 23.074875 | 95.037518% | 52/52 |
| 3 | 467.585282 | 20.235973 | 95.672239% | 52/52 |

For the distinct native-first-token metric, native receipt medians A/B are469.157490/17.361189ms,464.019172/21.809338ms,466.636802/18.897486ms. Do not relabel client-generated TTFT as native TTFT or mix these series. These are ratios of arm medians for each complete measured population, not a median of per-case ratios or an across-repetition confidence interval. The matched fixed1 observation does not repair failed128-token restore fidelity or establish allocator causality.

## Protected observations and controls

Independently recomputed native medians, full populations and exact paired output/work matches for small12,pageheavy,cold and decode controls. Positive numbers below mean latency increased or decode rate increased, as explicitly labeled.

| Lane/metric | Repetition1 | Repetition2 | Repetition3 | Paired coverage per repetition |
| --- | ---: | ---: | ---: | --- |
| small12 E2E latency change | -2.314463% | -5.863196% | **+8.196870%** | 24/24 full work matched |
| pageheavy client-generated TTFT change | -0.808990% | +0.559855% | **+13.105156%** | 12/12 full work matched |
| cold client-generated TTFT change | +0.881324% | +0.048926% | +0.570925% | 12/12 full work matched |
| decode-c1 per-request decode rate change | +0.038721% | +0.071020% | +0.172400% | 1/1 IDs/reasoning/body/prompt and whole-batch work matched |

Repetition3 small12 E2E medians are19.166578ms(A) and20.737638ms(B). Repetition3 pageheavy client-generated TTFT medians are33.724486ms(A) and38.144133ms(B). Both observed regressions exceed the frozen3% per-repetition protected-lane guardrail. This is an additional reason not to describe the candidate as meeting the complete performance contract; the strict analyzer itself still returns incomplete because correctness receipts are false. Cold's small observed shifts and C1's approximately192tokens/second rates are descriptive controls, not demonstrated statistical equivalence/parity. Each C1 arm/repetition has just one measured1024-token request.

## Decode and natural-output limits

For all12decode runs, independently checked native batch_speculative_work against the single normalized run vector. Each is valid with all four metric families/label support. It is compared once per whole drained batch, never copied or summed once per request. All three C1 pairs have equal batch vectors and identical1024-token ID sequences. All three C4pairs have unequal batch vectors and zero matching complete paired ID sequences among their four cases. C4 observed median per-request decode-rate deltas (+1.472121%,+0.238435%,-0.132227%) are therefore unmatched-work observations only; they do not show an allocator decode effect, matched throughput parity or aggregate batch throughput. Nested per-case complete_work_match is not authoritative for decode: the aggregate batch gate is controlling, and no conclusion here uses the known nested-case limitation.

Natural-output lanes retain empty output_token_ids for every measured row:312original-reverse,312original-forward and144small12-original across both arms/all repetitions. Therefore output equality is **unverified**, not proven unequal, and “output_unequal_cases” in the descriptive JSON must not be interpreted as a token mismatch count. Their complete_work_matches=0 is not evidence that every output token differs. E2E observations can be labeled unmatched natural-work diagnostics only.

## Binding gates and permitted conclusions

The12known `batch-before.prom: invalid request index` normalization issues remain visible, one per decode run. They are the frozen parser limitation, not12model-request failures; reviewed native closure proofs remain separate. Original false restoration/monitoring receipts, including r2-B's original target-binding failure and later supplemental closure, remain retained. No parser repair, smoothing, selective population or rerun was used in this review.

Strict comparison_gates_passed=false, performance_eligible=false, recommend_candidate=false and promoted=false are binding. All27strict pairs are incomplete, and the diagnostic amendment plus failed numerical/restore-fidelity/independent-oracle/causal gates prevent qualification even where one-token work matches. Permitted conclusions are the exact complete-population observations above, the increased row capacity with corresponding page-budget reduction, and bounded test/audit evidence already reviewed. No causal conclusion is made; raw causal observations are a separate pending interpretation. No hidden confidence claim, general allocator speedup, production recommendation or upstream readiness follows.

Only this note was written. No network, lifecycle, model request, GPU action, code/source edit, build, corpus rewriting or new benchmark occurred.
