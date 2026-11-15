# Offline descriptive matrix summary

Run only after the normalized matrix is complete and closed:

```sh
python3 analysis/describe.py analysis/normalized-matrix.json > analysis/descriptive.json
python3 analysis/check-describe.py
```

The result is always `diagnostic_only=true`, `performance_eligible=false`, and
`recommend_candidate=false`. It keeps every attempted, failed, and missing row;
timing is never filtered to output/work-matched survivors. A pair delta appears
only when both complete measured populations have finite metrics and no failed
measured request. Medians cover valid metric rows; p95 uses nearest rank
`ceil(0.95*n)`. Per-phase retained-delta sums carry their contributing and
expected counts. `flare_cache_reused_tokens_total` is retained as reported,
without assigning it exclusive GPU semantics; host reuse stays separate and the
two are never combined. Decode batch speculative work is retained once per arm,
validated and compared independently, never attributed to requests.

[Source-traced counter semantics](../review/cache-counter-semantics.md) explain
the clean serial-request accounting equation and why admission-time host reuse
alone does not prove transfer completion or exclusive tier provenance.

Per-case output equality and aggregate complete-work counts are separate. The
aggregate count requires successful requests, valid work evidence, and for
decode one valid equal batch vector. Root and per-run normalization issues,
receipts, and phase summaries remain visible beside the descriptive result.

Known bounded-review limitation: nested decode `pair.cases[*].complete_work_match`
flags exclude the batch check and can contradict the corrected aggregate count.
Do not use these nested flags in any conclusion or chart. Use the reviewed
aggregate totals and separate batch comparison; see
[the independent review](../review/descriptive-analysis-review.md). This
descriptive artifact never replaces the frozen strict analyzer.

The frozen normalizer also treats decode's legitimate `batch-before.prom` as
an invalid numeric request index. Retain that original normalization issue and
its resulting false decode phase gates. The closure assembler has a separately
reviewed, narrowly scoped handler for this declared batch artifact; its functional
proofs are reported alongside the raw normalized matrix. This does not remove
attempts, rewrite frozen inputs, or clear numerical/performance acceptance.
No second normalized matrix is needed for the unqualified descriptive report.
See [proof review](../review/proof-assembler-review.md).
