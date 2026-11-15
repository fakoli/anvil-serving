# FLARE-FIX-001 closed diagnostic record

The six-arm cache-row allocation experiment is closed. It does **not** qualify
the candidate, change the default, promote a serve, or submit an upstream
patch. The incumbent remains the recommendation. The fixed-work causal assessment was independently reviewed and accepted as
diagnostic causal support only. It remains outside qualification, default,
promotion, and general-performance decisions.

The candidate raises startup continuation-row allocation from the preserved
25% default to 75% within the same physical 32 GiB host-cache pool. Startup
verified A at 52 rows / 7,314 pages and B at 158 rows / 2,434 pages. The
startup option accepts 1–99; omission remains 25%, and changing it requires a
restart. The physical pool is 34,359,738,368 bytes in both arms; row/page
counts are allocation geometry, not simultaneous full-context capacity.

The formal AB, BA, AB matrix retained 2,550 formal requests (1,152 prime +
1,398 measured), plus 162 warmups across 54 fresh incarnations. There were no formal
request failures. All derived cells remain `performance_eligible=false`.

## Decision

Fixed-work, a serial one-token reverse-replay workload, observed a 95.038% to
96.065% lower median client-generated TTFT for B over three complete 52/52
output/work-matched populations. The accepted independent review rehashed 1,182 ledger inputs and verified 312
requests, 52 work matches per pair, and A=10/B=36 eligible completed fetches
for this exact workload. It also links the workload to 112→0 row-pressure
increments across prime plus replay and 199,468→2 measured prefill tokens. It
does not establish a general Flare or incumbent benefit.

Qualification remains blocked: the all-case sampled-output numerical gate
failed; protected repetition-3 small12 and pageheavy populations regressed by
8.197% and 13.105%; and decode C4 output/work populations are unmatched.
Natural-output lanes have no retained output token IDs, so their equality is
**unverified**, not proven unequal. The initial independent-oracle,
restore-fidelity, and causal proof receipts remain false.

## Matrix

Medians and p95 are derived from the complete measured populations. Latencies
are milliseconds; decode is tokens/second. p95 uses nearest rank
`ceil(0.95*n)`. Positive delta favors B for the lane metric. These are raw
diagnostic observations, not accepted performance results.

| Lane | Rep | Unit | A native N | A median / p95 | B native N | B median / p95 | Observed delta | Output/work state |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| original-reverse | 1 | ms | 52/52 | 2232.611 / 3155.010 | 52/52 | 1679.467 / 3092.464 | +24.776% | equality unverified (IDs absent) |
| original-reverse | 2 | ms | 52/52 | 2229.204 / 3156.222 | 52/52 | 1710.350 / 3082.410 | +23.275% | equality unverified (IDs absent) |
| original-reverse | 3 | ms | 52/52 | 2229.920 / 3154.062 | 52/52 | 1638.094 / 3068.251 | +26.540% | equality unverified (IDs absent) |
| original-forward | 1 | ms | 52/52 | 2406.638 / 3108.717 | 52/52 | 1418.142 / 2918.061 | +41.074% | equality unverified (IDs absent) |
| original-forward | 2 | ms | 52/52 | 2399.688 / 3093.179 | 52/52 | 1573.695 / 2344.282 | +34.421% | equality unverified (IDs absent) |
| original-forward | 3 | ms | 52/52 | 2403.207 / 3152.255 | 52/52 | 1521.556 / 2922.577 | +36.686% | equality unverified (IDs absent) |
| small12 | 1 | ms | 24/24 | 16.030 / 20.827 | 24/24 | 15.659 / 25.949 | +2.314% | 24/24 output/work matched |
| small12 | 2 | ms | 24/24 | 19.340 / 23.079 | 24/24 | 18.206 / 23.281 | +5.863% | 24/24 output/work matched |
| small12 | 3 | ms | 24/24 | 19.167 / 23.571 | 24/24 | 20.738 / 25.165 | -8.197% | 24/24 output/work matched |
| fixed-work | 1 | ms | 52/52 | 469.982 / 899.879 | 52/52 | 18.494 / 26.306 | +96.065% | 52/52 output/work matched |
| fixed-work | 2 | ms | 52/52 | 464.987 / 899.623 | 52/52 | 23.075 / 30.644 | +95.038% | 52/52 output/work matched |
| fixed-work | 3 | ms | 52/52 | 467.585 / 901.555 | 52/52 | 20.236 / 32.879 | +95.672% | 52/52 output/work matched |
| pageheavy | 1 | ms | 12/12 | 33.330 / 53.303 | 12/12 | 33.061 / 76.012 | +0.809% | 12/12 output/work matched |
| pageheavy | 2 | ms | 12/12 | 33.199 / 62.964 | 12/12 | 33.385 / 58.161 | -0.560% | 12/12 output/work matched |
| pageheavy | 3 | ms | 12/12 | 33.724 / 63.550 | 12/12 | 38.144 / 64.631 | -13.105% | 12/12 output/work matched |
| cold | 1 | ms | 12/12 | 61.882 / 62.572 | 12/12 | 62.427 / 63.246 | -0.881% | 12/12 output/work matched |
| cold | 2 | ms | 12/12 | 62.312 / 63.159 | 12/12 | 62.342 / 63.032 | -0.049% | 12/12 output/work matched |
| cold | 3 | ms | 12/12 | 62.012 / 63.052 | 12/12 | 62.366 / 63.400 | -0.571% | 12/12 output/work matched |
| decode-c1 | 1 | tok/s | 1/1 | 192.015 / 192.015 | 1/1 | 192.089 / 192.089 | +0.039% | 1/1 output/work; batch equal |
| decode-c1 | 2 | tok/s | 1/1 | 192.038 / 192.038 | 1/1 | 192.174 / 192.174 | +0.071% | 1/1 output/work; batch equal |
| decode-c1 | 3 | tok/s | 1/1 | 192.048 / 192.048 | 1/1 | 192.379 / 192.379 | +0.172% | 1/1 output/work; batch equal |
| decode-c4 | 1 | tok/s | 4/4 | 106.517 / 109.560 | 4/4 | 108.085 / 109.419 | +1.472% | unmatched 0/4; batch unequal |
| decode-c4 | 2 | tok/s | 4/4 | 109.196 / 110.215 | 4/4 | 109.456 / 109.887 | +0.238% | unmatched 0/4; batch unequal |
| decode-c4 | 3 | tok/s | 4/4 | 107.171 / 109.342 | 4/4 | 107.030 / 108.499 | -0.132% | unmatched 0/4; batch unequal |
| small12-original | 1 | ms | 24/24 | 1341.452 / 1735.142 | 24/24 | 1339.276 / 1742.159 | +0.162% | equality unverified (IDs absent) |
| small12-original | 2 | ms | 24/24 | 1344.510 / 1743.917 | 24/24 | 1344.733 / 1737.556 | -0.017% | equality unverified (IDs absent) |
| small12-original | 3 | ms | 24/24 | 1344.097 / 1735.787 | 24/24 | 1344.726 / 1967.618 | -0.047% | equality unverified (IDs absent) |

The frozen strict analyzer marks all 27 strict lane/repetition comparisons
incomplete. Twelve retained decode parser issues arise from the declared
`batch-before.prom` artifact and are not request failures. The reviewed closure
assembler supplies scoped functional evidence without changing the frozen
normalizer or qualifying the matrix.

## Scope and limits

The source pin is `47581ccac82660953a5b59cb04b2750d64edc97d`; v2 patch SHA-256
is `b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76` and
image digest is `sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411`.
Two TP2 GPUs ran at 300 W. The opt-in byte auditor passed its exercised A/B
copies but needs 32 MiB host scratch per rank (64 MiB total) outside the pool
and was disabled during formal timing.

Validation retains 44 focused tests, a 120-pass/0-fail/8-ignored CPU offload
receipt, generic and actual-model transfer audits, and a later CPU shared-fetch
lifetime fixture. That fixture is outside frozen v2. Missing evidence includes
a numerical component/teacher-forced oracle, Restore/re-Snapshot state oracle,
and live DMA cancellation overlap. The causal extractor used assert checks; do
not run it under `python -O`. This campaign used ordinary Python and the
independent rehash closed the campaign-specific risk; general reuse needs
explicit exceptions and bounded regular-file reads.

Nine restoration windows are retained. r1-B keeps its unknown outer-runner
exit and monitoring limitations. r2-B keeps its false default restoration proof
and separately accepted 54.9174-minute supplemental receipt. r3-B was
historically verified at 04:32:41Z after 49.7004166683 minutes; this is not a
claim of perpetual health.

Upstream was observed 132 commits ahead of the frozen source pin; no submission
was made. Before any default decision, isolate the protected regressions and
build an independent numerical component oracle. The current recommendation is
the incumbent.

Sanitized records: [summary](summary.json), [report](report.md),
[coverage and gaps](coverage-and-gaps.md), [restoration](restoration.json),
[normalized matrix](analysis/normalized-matrix.json.gz), [strict analysis](analysis/diagnostic-analysis.json),
[descriptive analysis](analysis/descriptive.json.gz), and [causal assessment](analysis/final-causal-assessment.md).


## Retained evidence and reconstruction

This bundle retains all six formal arms and failed attempts. Archives preserve
original evidence-relative member names; decompress each archive into the same
root for offline inspection. The three large JSON analyses use gzip.

[Artifact manifest](artifact-manifest.json) binds public bytes.
[Provenance](provenance.json) maps native source hashes to sanitized payloads
and archive members; native hashes are not hashes of redacted public bytes.
[Source aliases](publication/source-alias-map.json) preserve twelve legacy
plain-stdout files and three long filenames under safe publication names.
Recipes are reconstruction records, not executable private operator settings.
Use the existing managed recipe load/status/logs/unload commands and refresh
current source/host state before another run. No new plugin is required.

- [configuration-and-operations](raw/configuration-and-operations.tar.gz)
- [failed-windows](raw/failed-windows.tar.gz)
- [formal-r1-a](raw/formal-r1-a.tar.gz)
- [formal-r1-b](raw/formal-r1-b.tar.gz)
- [formal-r2-a](raw/formal-r2-a.tar.gz)
- [formal-r2-b](raw/formal-r2-b.tar.gz)
- [formal-r3-a](raw/formal-r3-a.tar.gz)
- [formal-r3-b](raw/formal-r3-b.tar.gz)
- [lifetime-and-transfer-fixtures](raw/lifetime-and-transfer-fixtures.tar.gz)
- [monitoring-and-readiness](raw/monitoring-and-readiness.tar.gz)
- [pilots-and-audits](raw/pilots-and-audits.tar.gz)
- [reviews-and-limitations](review/reviews-and-limitations.tar.gz)
- [frozen-source-and-harness](source/frozen-source-and-harness.tar.gz)

[Reviewable contribution](contribution/CONTRIBUTION.md) retains split patches, prerequisites and replay provenance; no upstream submission occurred.
