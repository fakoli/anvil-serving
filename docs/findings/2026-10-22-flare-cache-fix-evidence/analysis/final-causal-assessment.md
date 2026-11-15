# Fixed-work diagnostic causal assessment

The completed experiment supports the intended cache mechanism for this exact serial one-token reverse-replay workload. It remains unqualified: all strict gates and initial false causal receipts are preserved. The full numerical gate failed, and two protected lanes exceeded the regression limit.

| Pair | A median TTFT (ms) | B median TTFT (ms) | Reduction | Complete work matches |
| --- | ---: | ---: | ---: | ---: |
| 1 | 469.982 | 18.494 | 96.065% | 52/52 |
| 2 | 464.987 | 23.075 | 95.038% | 52/52 |
| 3 | 467.585 | 20.236 | 95.672% | 52/52 |

Every pair observed A 52 / B 158 rows and A 7,314 / B 2,434 pages at the same 32 GiB. Across the 52 priming plus 52 measured requests, row-pressure and record-eviction counter increments were 112 for A and 0 for B; page-pressure increments were 0 for both. These are endpoint counter differences, not the maxima in 15-second scrape samples.

Each 52-request measured replay has 10 clean completed eligible host restores for A and 36 for B. Native before/after admission, completed-transfer, cancellation/error, idle-state and prompt-accounting checks jointly establish these counts. Non-restoring requests remain in the denominator. Admission counters are not a count of unique historical token origin.

Measured prefill tokens were 199,468 for A and 2 for B in each repetition; host-extension tokens were 76,846 and 276,312, with the same 123,585 GPU-ready admission-reuse tokens. The complete measured populations match frozen bodies, prompt lengths, one output token and speculative-work counters. This is first generated-token latency, not first visible-answer latency or decode speed.

The 3% protected regression requirement is not met: repetition 3 small12 E2E regressed 8.197% and pageheavy TTFT regressed 13.105%, on complete matched populations. Their cause is not established. Natural-output lanes lack token-ID equality evidence; decode C4 has different outputs and whole-batch speculative work. No unmatched survivors were selected.

r1-B monitoring and r2-B restoration provenance exceptions remain explicit; historical supplementation does not rewrite the original failing receipts. Restore/state numerical oracles and physical cancellation-overlap coverage remain missing. No global default, candidate promotion, upstream submission or incumbent parity is justified.

Machine-readable observations, all 312 measured request checks and input hashes: [cache observations](cache-causal-observations.json.gz). Assessment references, exact numbers and original false receipts: [hash-linked assessment](final-causal-assessment.json). The strict result remains [non-accepting](diagnostic-analysis.json).
