# Flare cache-row allocation diagnostic

**Date:** 2026-10-22  
**Status:** closed diagnostic; `no-promotion`  
**Decision:** retain the incumbent. No default change, serve change, upstream submission, or promotion follows.

<!-- benchmark-result-card/v1 -->

> **Result card — bounded local observation.** On the same dual RTX PRO 6000
> Blackwell Max-Q TP2 hardware at 300 W per GPU, increasing Flare's startup
> continuation-row allocation from 25% to 75% retained the same 32 GiB physical
> host-cache pool. In one serial fixed-work reverse-replay lane, three complete
> 52/52 output/work-matched repetitions had 95.038–96.065% lower median
> client-generated TTFT for the candidate. Independent review accepted causal
> support for that exact lane only. The all-case numerical gate failed, protected
> lanes regress in repetition 3, and decode C4 is unmatched; every matrix cell
> remains performance-ineligible. Direct-online measurement used the managed
> reconstruction for this diagnostic: [managed configuration records](2026-10-22-flare-cache-fix-evidence/raw/configuration-and-operations.tar.gz). [Evidence index](2026-10-22-flare-cache-fix-evidence/README.md)
> · [artifact manifest](2026-10-22-flare-cache-fix-evidence/artifact-manifest.json)
> · [decision summary](2026-10-22-flare-cache-fix-evidence/summary.json)

## Scope and configuration

This six-arm AB/BA/AB diagnostic retained 2,550 formal requests (1,152 prime +
1,398 measured), plus 162 warmups over 54 fresh incarnations. No formal request
failed. The source pin was `47581ccac82660953a5b59cb04b2750d64edc97d`; the v2
patch SHA-256 was `b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76`,
and the image digest was
`sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411`.

The control used the preserved 25% startup row allocation (52 rows / 7,314
pages). The candidate used 75% (158 rows / 2,434 pages). Both configurations
use the same 34,359,738,368-byte physical host-cache pool. These counts describe
allocation geometry, not simultaneous full-context capacity. The startup option
accepts 1–99, defaults to 25% when omitted, and requires a restart.

The model was GLM-5.3-Flash CSF/QAD NVFP4-MXFP8 at revision
`dec48abd33efa73c3bb7c95b74eee10cad34f9be`; measurement used two RTX PRO 6000 Blackwell Max-Q GPUs in TP2 at
300 W each. The optional byte auditor passed its exercised A/B transfers but
used 64 MiB total host scratch outside the fixed pool and was disabled during
timing.

## Measured matrix

Medians and p95 values use complete measured populations. `original-*` and
`small12` / `small12-original` are E2E latency; `fixed-work`, `pageheavy`, and `cold` are
first-generated-token TTFT; decode is per-request tokens/second, not aggregate
throughput. Latencies are milliseconds. p95 uses nearest rank, `ceil(0.95*n)`.
Positive delta favors the candidate for the lane metric. These values are raw
diagnostic observations, not accepted performance results.

| Lane | Rep | Unit | Control N, median/p95 | Candidate N, median/p95 | Raw delta | Output/work state |
| --- | ---: | --- | --- | --- | ---: | --- |
| original-reverse | 1 | ms | 52/52, 2232.611/3155.010 | 52/52, 1679.467/3092.464 | +24.776% | equality unverified; IDs absent |
| original-reverse | 2 | ms | 52/52, 2229.204/3156.222 | 52/52, 1710.350/3082.410 | +23.275% | equality unverified; IDs absent |
| original-reverse | 3 | ms | 52/52, 2229.920/3154.062 | 52/52, 1638.094/3068.251 | +26.540% | equality unverified; IDs absent |
| original-forward | 1 | ms | 52/52, 2406.638/3108.717 | 52/52, 1418.142/2918.061 | +41.074% | equality unverified; IDs absent |
| original-forward | 2 | ms | 52/52, 2399.688/3093.179 | 52/52, 1573.695/2344.282 | +34.421% | equality unverified; IDs absent |
| original-forward | 3 | ms | 52/52, 2403.207/3152.255 | 52/52, 1521.556/2922.577 | +36.686% | equality unverified; IDs absent |
| small12 | 1 | ms | 24/24, 16.030/20.827 | 24/24, 15.659/25.949 | +2.314% | 24/24 output/work matched |
| small12 | 2 | ms | 24/24, 19.340/23.079 | 24/24, 18.206/23.281 | +5.863% | 24/24 output/work matched |
| small12 | 3 | ms | 24/24, 19.167/23.571 | 24/24, 20.738/25.165 | -8.197% | 24/24 output/work matched |
| fixed-work | 1 | ms | 52/52, 469.982/899.879 | 52/52, 18.494/26.306 | +96.065% | 52/52 output/work matched |
| fixed-work | 2 | ms | 52/52, 464.987/899.623 | 52/52, 23.075/30.644 | +95.038% | 52/52 output/work matched |
| fixed-work | 3 | ms | 52/52, 467.585/901.555 | 52/52, 20.236/32.879 | +95.672% | 52/52 output/work matched |
| pageheavy | 1 | ms | 12/12, 33.330/53.303 | 12/12, 33.061/76.012 | +0.809% | 12/12 output/work matched |
| pageheavy | 2 | ms | 12/12, 33.199/62.964 | 12/12, 33.385/58.161 | -0.560% | 12/12 output/work matched |
| pageheavy | 3 | ms | 12/12, 33.724/63.550 | 12/12, 38.144/64.631 | -13.105% | 12/12 output/work matched |
| cold | 1 | ms | 12/12, 61.882/62.572 | 12/12, 62.427/63.246 | -0.881% | 12/12 output/work matched |
| cold | 2 | ms | 12/12, 62.312/63.159 | 12/12, 62.342/63.032 | -0.049% | 12/12 output/work matched |
| cold | 3 | ms | 12/12, 62.012/63.052 | 12/12, 62.366/63.400 | -0.571% | 12/12 output/work matched |
| decode-c1 | 1 | tok/s | 1/1, 192.015/192.015 | 1/1, 192.089/192.089 | +0.039% | 1/1 output/work; batch equal |
| decode-c1 | 2 | tok/s | 1/1, 192.038/192.038 | 1/1, 192.174/192.174 | +0.071% | 1/1 output/work; batch equal |
| decode-c1 | 3 | tok/s | 1/1, 192.048/192.048 | 1/1, 192.379/192.379 | +0.172% | 1/1 output/work; batch equal |
| decode-c4 | 1 | tok/s | 4/4, 106.517/109.560 | 4/4, 108.085/109.419 | +1.472% | unmatched 0/4; batch unequal |
| decode-c4 | 2 | tok/s | 4/4, 109.196/110.215 | 4/4, 109.456/109.887 | +0.238% | unmatched 0/4; batch unequal |
| decode-c4 | 3 | tok/s | 4/4, 107.171/109.342 | 4/4, 107.030/108.499 | -0.132% | unmatched 0/4; batch unequal |
| small12-original | 1 | ms | 24/24, 1341.452/1735.142 | 24/24, 1339.276/1742.159 | +0.162% | equality unverified; IDs absent |
| small12-original | 2 | ms | 24/24, 1344.510/1743.917 | 24/24, 1344.733/1737.556 | -0.017% | equality unverified; IDs absent |
| small12-original | 3 | ms | 24/24, 1344.097/1735.787 | 24/24, 1344.726/1967.618 | -0.047% | equality unverified; IDs absent |

## Bounded causal observation

The fixed-work lane is a serial one-token reverse replay. Its three 52/52
output/work-matched populations observed 95.038–96.065% lower median
client-generated TTFT for the candidate. Independent review accepted diagnostic
causal support for this exact lane after rehashing 1,182 ledger inputs and
verifying 312 requests, 52 work matches per pair, 10→36 eligible completed
fetches, 112→0 row-pressure increments across prime plus replay, and
199,468→2 measured prefill tokens.

This does not establish a general Flare or incumbent advantage. It does not
clear a numerical gate, qualify a configuration, justify changing a default, or
authorize promotion. The causal extractor used assertions; it must not be run
under `python -O`. The campaign used ordinary Python and the independent rehash
covers this campaign; general reuse needs explicit exceptions and bounded
regular-file reads.

## Gates, retained failures, and limits

The all-case sampled-output numerical gate failed. Requested zero temperature
clamps to approximately 0.95 in the retained package, and an omitted seed
defaults to zero; neither fact waives divergence. A numerical component or
teacher-forced/logit oracle and a Restore/re-Snapshot state oracle are missing.

The frozen strict analyzer marks all 27 lane/repetition comparisons incomplete.
Twelve decode parser issues from the declared batch artifact remain retained and
are not request failures. The third protected `small12` repetition regressed
8.197%, and protected `pageheavy` regressed 13.105%. Decode C4 has unequal
output/work and whole-batch vectors in every repetition. Natural-output lanes
lack token IDs, so output equality is unverified rather than proved unequal.
Every matrix cell is `performance_eligible=false`.

Validation retains 44 focused tests, a 120-pass/0-fail/8-ignored CPU offload
receipt, generic and actual-model transfer audits, and a later CPU shared-fetch
lifetime fixture. The fixture is outside frozen v2. Live DMA cancellation
overlap remains unproven.

Nine restoration windows are retained. r1-B retains an unknown outer-runner
exit and monitoring limitations. r2-B retains its false default restoration
proof plus a separately accepted 54.9174-minute supplemental receipt. The
final r3-B restoration was historically verified at 04:32:41Z after
49.7004166683 minutes; it is not a future-health claim.

## Evidence and next step

The public-safe evidence bundle is retained at
[`2026-10-22-flare-cache-fix-evidence/`](2026-10-22-flare-cache-fix-evidence/README.md).
It retains the evidence index,
summary, source registry, normalized/descriptive outputs, raw native receipts,
failures, restoration records, and reviews. The campaign does not use an
external prior as promotion-quality evidence.

Keep the incumbent. Before any default decision, build an independent numerical
component oracle and isolate the protected-lane regressions. Upstream was
observed 132 commits ahead of the frozen source pin; no upstream submission,
commit, or promotion occurred.
