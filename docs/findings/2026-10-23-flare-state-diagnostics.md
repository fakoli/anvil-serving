# Flare state-copy and protected-latency follow-up

**Date:** 2026-10-23 UTC  
**Status:** closed bounded diagnostic; `compatibility-only`, `no-promotion`  
**Decision:** retain the incumbent and the 25% default. Numerical and performance qualification remain held.

<!-- benchmark-result-card/v1 -->

> **Result card — local diagnostics.** On two RTX PRO 6000 Blackwell Max-Q
> GPUs in TP2 at unchanged 300 W limits, the native Snapshot → Restore →
> re-Snapshot test passed all six rank checks across three prompt endpoints.
> The protected A/B/B/A bracket completed 264/264 requests (144 measured).
> Four separate scheduler captures covered 72/72 measured requests and 144
> two-rank samples with no drops. These tests narrowed the investigation;
> they did not repair the historical numerical failure or qualify a faster
> configuration. The state oracle used reduced geometry; the serving tests
> used the same 32 GiB host cache with 25% versus 75% row allocation.
> Experiment HTTP traffic was direct to the candidate; routed probes verified
> incumbent restoration. [Evidence index](2026-10-23-flare-state-diagnostics-evidence/README.md)
> · [artifact manifest](2026-10-23-flare-state-diagnostics-evidence/artifact-manifest.json)
> · [decision summary](2026-10-23-flare-state-diagnostics-evidence/summary.json)
> · [managed configuration reconstruction](2026-10-22-flare-cache-fix-evidence/raw/configuration-and-operations.tar.gz)

## What was completed

The [preceding allocation experiment](2026-10-22-flare-cache-fix.md) implemented
an opt-in startup row-share setting and demonstrated a workload-specific cache
benefit. This follow-up completed the state-copy oracle, a bounded top-20
probability comparison, a counterbalanced protected-latency bracket, and separate
scheduler traces. The pinned candidate timing image remained unchanged across the protected bracket.

Two test-only repairs corrected CUDA-context ownership in the new native oracle.
The initial direct-rank read left the calling thread in rank 1's context before
its rank 0 executor submitted Restore. The repaired fixture restores the caller
context and validates it before submission and shutdown. Its failed attempt and
both repairs remain in the evidence. This fixture failure is not evidence that
production Restore corrupts state.

The experiment controller also learned to reconcile the exact stopped incumbent
before restoration: a stopped container has no live cgroup and can return a
non-success status for unavailable effective memory limits. Identity, configured
limits and clean exit still have to match; effective containment must pass again
when running. The original failed wrapper closeout and subsequent managed manual
recovery remain retained.

## Pinned setup and measurement boundaries

- **Model:** `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD`, revision
  `dec48abd33efa73c3bb7c95b74eee10cad34f9be`; served name
  `glm53-flare-47581cca-tp2-c4-mtp3`.
- **Flare:** base `47581ccac82660953a5b59cb04b2750d64edc97d`, local v2 patch
  `b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76`, image
  `sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411`.
- **Hardware and topology:** one inference host, two 96 GB RTX PRO 6000 Blackwell
  Max-Q GPUs, TP2. Power limits stayed at 300 W per GPU; power is not an assigned
  explanation for the observations.
- **Serving configuration:** concurrency ceiling 4, two MTP draft tokens,
  adaptive verification disabled, 32 GiB physical host-cache pool. A allocates
  52 rows / 7,314 pages; B allocates 158 rows / 2,434 pages. The recipe's 32,768
  tokens is the workload ceiling, not a measured native context limit. Transfer
  byte auditing was off during timing. KV/state layouts remain those of the
  retained signed package; this wave did not change their dtype.
- **Workload:** serial warmed protected requests after retained warmup/prime
  phases, one generated token per measured request. These are full GPU reuse
  observations with zero measured prefill, host fetch, and speculative-work
  deltas. They are separate from the prior host-restore benefit lane.

## Protected latency remains unresolved

All four fresh incarnations per lane ran in A/B/B/A order: 24 warmups,
96 prime and 144 measured requests, with no failed or missing request. Every
paired case matched output IDs and work. The table pools both incarnations of
an arm within each lane; p95 is nearest-rank `ceil(0.95*n)`. Values are
milliseconds and descriptive, not qualified performance results.

| Lane / metric | Measured N, A / B | A median / p95 | B median / p95 |
| --- | ---: | ---: | ---: |
| small12 E2E | 48 / 48 | 17.037 / 24.824 | 20.010 / 28.381 |
| pageheavy generated-token TTFT | 24 / 24 | 32.823 / 57.309 | 35.586 / 55.678 |

The per-case B-minus-A median changes in the two chronological pairs were
+17.356% and +9.704% for small12, and −6.128% and +7.245% for pageheavy.
These paired percentages differ from a ratio of pooled medians. Broad signed
ranges remain; neither noise nor allocation is proven to be the cause. The
historical 3% protected-regression threshold is unchanged.
[Exact populations, metric paths and pairs](2026-10-23-flare-state-diagnostics-evidence/analysis/protected-abba-2.json).

## State and probability observations

At endpoints 512, 513 and 7708, the native test compared 81,464,576 canonical
snapshot bytes per rank per endpoint. Source guards, poisoned destinations,
exact Snapshot A/B equality and final drain all passed on both GPUs. The test
used four snapshot rows and 1024 logical pages with sampling disabled and no
host offload. It does not establish every production graph/geometry, live-row
padding equality, numerical recomputation, or DMA cancellation behavior.

Two first/repeat HTTP pairs each emitted the same eight token IDs. Case 40's
returned top-20 logprob entries were exact. Case 51's top-20 logprobs differed at
positions 3–7, with minimum overlap 18/20 and a largest common-entry absolute
logprob difference of 0.500295639. This is not a full-distribution bound.
Case 51 also had different batch-shape counter distributions despite equal
aggregate speculative work, so the difference cannot be attributed solely to
state, caching, MTP acceptance or a specific kernel.
[State and top-20 evidence](2026-10-23-flare-state-diagnostics-evidence/analysis/state-logprob.json).

The signed package does not expose a verified semantic full-logit readback
contract. Full-vocabulary logits, teacher-forced independent reference, relative
L2 and KL results remain missing. The earlier exact 128-token gate remains
failed; eight matching sampled IDs do not clear it.

## What the scheduler trace can tell us

Four separate approximately 30-second captures covered all 72 measured HTTP
requests and 144 Restore/Resume samples. Every sample contains both ranks, with
no dropped updates or boundary exclusions. All 36 A/B request pairs match
bodies, outputs and recorded work. A/B graph-shape mixtures nevertheless differ,
and HTTP requests are not mapped to native scheduler slots/generations.

The captures retain selection, commit, observation, wall and GPU-event timings.
They do not identify a specific production bottleneck or establish a causal
latency difference. Rank intervals overlap and must not be summed; their spread
is not synchronization wait, and wall minus GPU time is not pure CPU overhead.
[Normalized trace evidence](2026-10-23-flare-state-diagnostics-evidence/analysis/trace-paired-1/trace-paired-1-summary.json).

Source inspection found an avoidable host-index search on a fully GPU-cached
admission. A shared `Index::lookup_where` early return when `resident >= total`
could skip a provably empty search. Its cost depends on prefix-bucket occupancy,
not directly on configured row capacity. No production guard patch or measured
benefit was produced; the source review is in the
[review archive](2026-10-23-flare-state-diagnostics-evidence/archives/reviews.tar.gz).

## Verification, restoration and remaining work

Independent reviews accepted the bounded state and trace evidence and held
numerical qualification, protected-performance qualification, tuning selection
and promotion. Five native compile-only passes are build evidence; the three
successful GPU endpoint processes and their six rank checks are execution
evidence. Failed/interrupted windows remain in the raw archives.

The broad product test retry stopped at an existing Connect user-memory fixture
error after 857 passes, five skips and 62 passing subtests. The earlier temporary
path custody failure and unavailable Ruff module are retained; the established
`uvx ruff` invocation passed. These results do not support an all-tests-passed
claim or change the scoped GPU diagnostic findings.

The exact retained incumbent was restored with direct and routed preflight,
monitoring incarnation, effective RAM containment, zero restart/OOM state and a
ready/admitting idle router verified at 07:32:29 UTC. Only its two tensor-parallel
workers remained on the GPUs. This is a dated observation, not future-health
proof. No default change, promotion, upstream submission, commit or push occurred.

The next useful measurements are explicit HTTP-to-scheduler correlation and
admission/host-index spans, captured separately from timing, plus a separately
bound package export for full pre-sampling logits. These are deferred work, not
completed fixes. The present result supports continuing a focused investigation;
it does not justify converting the incumbent to Flare.

Raw archives and native JSON retain source-relative evidence paths for provenance;
those embedded paths are not website links. Use this finding's links, the bundle
index and the archive member names to navigate the published records.
