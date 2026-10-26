# Split top-p numerical reference and conservative gate

Observed: 2026-10-25. This is a source-derived, preweight diagnostic design. It does not record a model inference result or authorize model contact, lifecycle changes, routing changes, or promotion.

## Source basis

- vLLM PR [#58231](https://github.com/vllm-project/vllm/pull/58231), lines 165-179: the split CUDA path found its pivot in probability space but reconstructed a logit-space threshold; at very large logits fp32 cancellation could mask the entire row. The narrow fix masks with the probability-space predicate and keeps the row maximum.
- Merged commit [`0a1276e5e3d9000488e7590cc0957edd709f2152`](https://github.com/vllm-project/vllm/commit/0a1276e5e3d9000488e7590cc0957edd709f2152), commit page lines 143-170 and test diff lines 184-210: exact upstream patch and forced-logit regression coverage.
- Official [sort implementation](https://raw.githubusercontent.com/vllm-project/vllm/0a1276e5e3d9000488e7590cc0957edd709f2152/vllm/v1/sample/ops/topk_topp_sampler.py), lines 419-457: sort, softmax, cumulative low-tail mask, and at-least-one-token rule.
- Official [Triton comparison tests](https://raw.githubusercontent.com/vllm-project/vllm/0a1276e5e3d9000488e7590cc0957edd709f2152/tests/v1/sample/test_topk_topp_sampler.py), lines 268-310: comparison to the sort implementation and the existing count-compatibility envelope. Lines 281-290 construct both results and cast `p` to fp32; lines 291-309 compare kept counts and permit a batch maximum difference of three, otherwise less than 0.5% of that batch's maximum reference kept count.
- vLLM issue [#59964](https://github.com/vllm-project/vllm/issues/59964), lines 154-169: the bounded split search can over-keep near the boundary, and its absolute `1e-9` tie bands can merge distinct fp32 probabilities. Therefore exact minimality is not the existing split-kernel contract.
- Pinned source `vllm/v1/sample/ops/topk_topp_triton.py`: lines 984-987 fix the split bounds and round count; lines 1128-1139 and 1185-1198 document the mass-preserving fallback; lines 1315-1328 show fp32 probability and tie calculations; lines 1481-1493 and 1504-1513 implement deterministic index-ordered duplicate handling; lines 1658-1660 cast runtime `p` to fp32; lines 1664-1678 select the CUDA split path.

The `7.7e-5` mass figure sometimes associated with this patch is external, not local: it appears in a third-party PR comment at PR #58231 lines 390-401, specifically line 400, for a separate sm110 experiment. The earlier retained local preweight diagnostic established only that stock and patched masks differed in one ordinary case; it did not establish the difference's cardinality or probability mass. The external figure is not an acceptance tolerance.

## Independent binary64 numerical reference

This oracle is a deterministic double-precision numerical reference, not an exact-real nucleus proof:

1. Preserve the original finite fp32 logits and the actual runtime `p` after fp32 conversion. Convert those fp32 values to Python `float`; do not substitute a decimal binary64 `p`.
2. Stable-sort token indices by `(logit descending, token_id ascending)`. The token-id rule canonicalizes exact-logit ties consistently with the kernel's index-ordered duplicate budget.
3. Subtract the maximum logit and compute binary64 weights with `math.exp`.
4. Compute the normalizer with `math.fsum(weights)` and set `target = float(p_fp32) * normalizer`. Comparing unnormalized prefix weight with `target` avoids a second rounding step per probability.
5. Locate the smallest `candidate_count` whose prefix reaches `target` with one compensated (Kahan or Neumaier) cumulative scan. Certify only the boundary using `math.fsum(weights[:candidate_count])` and, when `candidate_count > 1`, `math.fsum(weights[:candidate_count - 1])`. The candidate is valid only when the former is at least `target` and the latter (or zero for a one-token prefix) is below it.
6. If the compensated scan and the two `math.fsum` checks do not certify the same boundary, report `oracle_boundary_ambiguous` and stop. Do not adjust a tolerance from the observed discrepancy.

This is linear-time in the vocabulary apart from sorting and two final linear `fsum` checks. It avoids quadratic repeated-prefix summation while making the numerical boundary convention explicit. Reject non-finite ordinary inputs, invalid `p`, missing stock/patch outputs, source-hash drift, or mismatched input bytes.

## Predeclared gate

Freeze the source hashes, patch hash, seed/shape/`p` matrix, case order, and resource limits before execution. Retain the previous local case as reproduction evidence, but include an independent ordinary control such as the upstream seed `42`; do not adapt cases or thresholds after seeing results.

Forced cases must all retain the forced token, produce no NaN or all-masked row, select the forced argmax, and—on the CUDA split path—retain exactly one token.

For every ordinary row:

1. Stock and patch results must be readable and derived from identical input bytes. If stock omits any oracle token, stop; do not use that row to define a stock-relative tradeoff.
2. The patched kept set must be a canonical prefix under `(logit descending, token_id ascending)` and must contain the entire oracle set. Any hole, noncanonical exact tie, or missing oracle token stops the experiment.
3. Apply a conservative per-row form of the upstream count envelope: absolute kept-count difference from the oracle must be at most three; above three, the difference divided by the oracle kept count must be strictly below `0.005`.
4. The patched symmetric-difference count from the oracle must be no greater than stock's for that same row. Do not average improvements and regressions across rows.

Report exact mask equality and whether removing the lowest-priority kept token drops the reference prefix below `p`, but treat these as diagnostics. Passing proves only that the forced masking defect is fixed without a detected regression against this bounded ordinary-row contract. It does not prove exact top-p behavior generally. Any under-keep, non-prefix result, stock under-keep, count-bound failure, symmetric-difference regression, ambiguous oracle boundary, timeout, or evidence drift stops before model inference; no tolerance may be tuned from that failure.

## Breakage probes

- Unreadable state: missing or unparsable stock/patch output, mismatched hashes, or absent original logits must fail closed.
- Boundary input: exact ties, adjacent fp32 logits, huge finite logits, `p` represented only after fp32 conversion, and non-finite ordinary logits must exercise the declared rejection or canonicalization behavior.
- Bounds: the case matrix, maximum vocabulary/batch, execution timeout, and retained artifact size must be fixed; there is no adaptive rerun.
- Drift: stock and patch consume the same captured input bytes, and the receipt binds source, patch, harness, case matrix, and kernel-path evidence. A changed source, configuration, or dispatch path invalidates comparison.
