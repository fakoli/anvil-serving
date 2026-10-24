# Final causal assessment procedure

## Boundary

This is a closure procedure, not a partial performance analysis. Run it only after the six formal arm directories (`r1-a`, `r1-b`, `r2-a`, `r2-b`, `r3-a`, and `r3-b`) are closed and their retained inputs are immutable. The matrix is ordered AB, BA, AB by `harness/prospective.json`; use its recorded order rather than inferring order from directory names.

Do not alter the existing per-lane `causal_chain.json` files or their `proofs.json` indexes. They intentionally record the initial false state. `analysis/assemble-proofs.py` also refuses to replace different existing proof bytes. A later, hash-linked closure assessment may state only **diagnostic causal support** or **diagnostic causal failure**, while linking those original false receipts and the raw/normalized inputs. It must not rewrite a false gate into a passing qualification receipt.

## Inputs and required fields

Use the frozen contracts together:

| Purpose | Required source |
| --- | --- |
| Arm/lane/repetition contract, order, metric, and protected/primary status | `harness/prospective.json` |
| Native attempts, phase summaries, plan/status, identities, and normalization issues | `harness/normalize.py`; `formal/r{1,2,3}-{a,b}/<lane>/` retained phase files, especially `http/attempts.json`, `http/summary.json`, wrapper `summary.json`, plan, status, and running receipts |
| Capacity/allocation identity | frozen `candidate-freeze.json` and `geometry.json`, plus the matching arm's native identity/plan/registration and capacity receipts |
| Pressure and completed-reuse evidence | matching arm `query-ranges/` native metrics and `http/attempts.json[].deltas` |
| Monitoring provenance and restoration containment | each arm's `analysis/readiness-history/`, retained monitoring samples/ranges, and closeout/restoration receipts |
| Full-population work/output and latency rules | `harness/analyze.py`, `analysis/describe.py`, and the completed normalized matrix made by `harness/normalize.py` |

For every pair, retain the native metric name, query range, timestamps, and provenance. Do not rename an observed metric into a presumed pressure measure. The final assessment must name the actual raw row-pressure field(s) present in the retained range evidence; if none supplies the needed contrast, causal support fails.

The attempt deltas usable for the reuse portion are `flare_prefill_tokens_total`, `flare_generated_tokens_total`, `flare_cache_reused_tokens_total`, `flare_host_cache_reused_tokens_total`, `flare_host_cache_fetched_total`, `flare_host_cache_fetch_failed_total`, `flare_host_cache_stored_total`, and `flare_host_cache_store_failed_total`. Include their contributing and expected request counts. `flare_cache_reused_tokens_total` is generic cache reuse: it is not evidence that GPU reuse and host reuse are disjoint, so do not add it to host reuse as independent work. A fetch is not completed reuse by itself; failures and requests without successful completion remain in the denominator.

## Closure sequence

1. Normalize the complete six-arm corpus with the frozen normalizer and retain its source/config hashes, root and run normalization issues, receipts, and native wrapper/error statuses. Reject an arm or pair whose frozen identity, plan, image/config, registration, or required native phases do not match the intended comparison.
2. For each AB/BA/AB repetition and applicable lane, establish the configuration contrast from frozen capacity/allocation evidence: the candidate's row/page geometry and admission allocation must differ as intended while all pinned comparison identities remain valid. A configured geometry alone is not an observed capacity result.
3. From the retained native range metrics, show the corresponding row-pressure contrast. Then use successful, completed native attempt records to show eligible fetch/reuse behavior, including fetch/store failures and partial delta coverage. Never substitute HTTP success, a cache lookup, or a sampled metric for completed reuse.
4. Evaluate the lane's planned latency metric only on the complete measured populations. The two arms must have identical case-ID sets and satisfy `harness/analyze.py` work/output matching: body hash, prompt tokens, output token IDs, reasoning tokens/span, and speculative-work contract. Decode additionally requires one valid equal whole-batch vector per run; it is not attributed to individual requests. Preserve expected, attempted, failed, and missing counts. Do not drop unmatched or failed rows to create matched survivors.
5. Record the pair-level result as diagnostic causal support only when all four links are evidenced in order: changed observed capacity/allocation, changed relevant row pressure, completed eligible reuse, and a complete matched latency population. Record diagnostic causal failure when any link is absent, contradictory, partial, or unmatched; state which link failed and link the retained raw evidence. Keep descriptive/original lanes descriptive even when their available latency summaries are present.

## Gate interpretation

The final strict analyzer must remain non-accepting regardless of any later diagnostic causal pattern. `diagnostic-amendment.json` forces diagnostic-only handling and `recommend_candidate=false`; `harness/normalize.py` forces `restore_fidelity=false` while that amendment exists, and the retained numerical-equivalence failure leaves the numerical gate false. The initial `independent_oracles`, `restore_fidelity`, and `causal_chain` receipts therefore remain false. A closure assessment can explain evidence quality and causal support/failure, but cannot make the campaign performance-eligible, recommend the candidate, or set `promoted=true`.
