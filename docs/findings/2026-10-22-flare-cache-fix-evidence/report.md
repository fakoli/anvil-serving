# FLARE-FIX-001 closed diagnostic report

## Executive outcome

**Status: execution closed; independent review accepted fixed-work diagnostic causal support only. Publication packaging remains pending. No qualification, default change, promotion, upstream submission, or general performance recommendation.**
The candidate keeps the total host-cache budget at 32 GiB while changing the startup row split from the preserved 25% default to a frozen 75% candidate. Actual signed-model startup verified 158 rows and 2,434 pages, compared with 52 rows and 7,314 pages for the control. The baseline pilot established that exact 128-token sampled-output fidelity is currently failed even without a host fetch or when a full GPU-cache hit occurs. Candidate A/B results remain unqualified diagnostics. Actual transfer-byte audits now pass; physical DMA cancellation overlap and the independent numerical/state oracles remain unproven.

Source pin: `47581ccac82660953a5b59cb04b2750d64edc97d`.

## What changed

The local candidate adds a configurable startup row percentage (`1..99`), with an omitted value preserving 25%; the frozen candidate uses 75%. The v2 source patch is `b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76` and its image is `sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411`. It adds opt-in, exact source-before/destination-after payload comparison and rejects mixed tiers.

## Tests and limits

Completed evidence includes 44 focused tests, 120 CPU offload tests (the earlier 118-test receipt is superseded), a generic two-GPU 25% byte round trip, and a two-GPU audit test that passed exact bytes for both 25%/75% splits, ranks, and directions with zero failures. `audit-a-startup-r3` confirms the actual signed model/stored-data verification and audit-enabled startup with 32 MiB host scratch per rank outside the fixed 32 GiB pool. `audit-a-correctness` passed 3/3 independent oracles and `audit-a-warmup` passed 3/3.

Sampling is stochastic in the retained package: requested temperature 0 clamps to about 0.95 and an omitted seed already selects 0. The earlier package metadata read was metadata-only and is not signature proof; `audit-a-startup-r3` separately verifies the actual signed model/stored data for the audit window. Both actual-model transfer audits passed every exercised rank/outbound/inbound page-and-row check with zero failures. This byte-transport evidence does not satisfy the failed sampled-output gate or the still-unimplemented teacher-forced/logit oracle. The numerical root cause remains unresolved.

The temperature clamp alone does not explain different outputs with a fixed seed. The pinned upstream numerical policy documents arrival-ordered atomic MoE reductions that can change logits and sampled tokens even for identical graph inputs and RNG state. This is a candidate mechanism, not a measured root cause or a waiver: see `review/numerical-policy-addendum.md`. The original exact-128 gate remains failed.

Independent review accepted `review/final-causal-review.md` (SHA-256 `473a96eddeb730e470ef9e468ba786f528597dfab4a6d862e205ae0cfd6fa9de`) as diagnostic causal support only: it independently rehashed 1,182 inputs and verified the fixed-work 312-request ledger, 52 work matches per pair, and A=10/B=36 eligible completed fetches. The extractor used assertions, so it must not be run under `python -O`; ordinary Python was used for this campaign, and general reuse needs explicit exceptions and bounded regular-file reads.

After freezing the runtime image, a separate test-only delta added a composed CPU Worker/scheduler fixture: two waiters share one fetch, the first is cancelled, resource pressure is applied, and successful/failed completion paths preserve ownership and eventual reclamation. The exact test passed and received independent review (`lifetime-test-execution/r2/execution.json`, `review/lifetime-fixture-review.md`). The first attempt failed compilation because the command omitted the existing `runtime-package` feature; that failure remains retained. This later test does not change the frozen v2 image and proves neither physical DMA overlap nor model fidelity.

## Experiment table

| Arm | Row/page split | Status | Performance eligibility | Raw evidence |
| --- | ---: | --- | --- | --- |
| Control | 25%: 52 / 7,314 | pilots and formal r1-A completed: 425 targets, 27 warmups, nine fresh generations, no failures | no; exact 128-token gate failed | `pilot-a-restore-comparison.json`, `pilot-a-repeatability-comparison.json`, `formal/r1-a/parent-review.json` |
| Candidate | 75%: 158 / 2,434 | actual-model byte audits passed; B fixed-1 pilot passed 52/52 IDs with 50 fetches | no; pilot is not fair timing and sampled-output/teacher-forced/logit gates remain open | `candidate-freeze.json`, `geometry.json`, `audit-a-transfer-audit.json`, `audit-b-transfer-audit.json`, `audit-b-fixed1-pilot.json` |
| AB, BA, AB | control/candidate pairs | all six formal arms complete and restored; no formal request failures | no; matrix is diagnostic only; numerical/fidelity/oracle gates remain false and causal support is accepted only for fixed-work | `formal/r1-a/` through `formal/r3-b/`; `analysis/final-causal-assessment.md`, `review/final-causal-review.md` |

Do not fill a recommendation from partial survivor subsets. The final table must retain every attempt, output/work mismatch, finish reason, and failure classification.

The completed matrix contains 2,550 formal requests (1,152 prime and 1,398
measured) and 162 warmups across 54 fresh formal incarnations, with no formal
request failures. This establishes corpus coverage only, not qualified
performance. A retained failed r2-B pretraffic window ran for 8.94875 minutes
and sent zero formal warmups or targets; it remains part of the failed-window
record rather than being folded into the completed-arm denominator.

The full-corpus normalizer, strict analyzer, and descriptive summary now exist
under `analysis/`. Their retained SHA-256 values are respectively
`5d47ecba13a6b7cc089ebe7fcb07018bcf94cc872e38190c30d1896bb6209143`,
`0ca23252e62b7bfeb2b75c6679dcae201fde494f9574f020d56d48b0d0597c76`,
and `2a7d15b0443c6f5e3124a10d46c2ff0ccaf3faea5fedafa1dcb383707a70eca9`.
They do not resolve the false acceptance gates. Independent review accepted
diagnostic causal support only for fixed-work; publication packaging remains
pending.

## Failure ledger

| Evidence | Result | Consequence |
| --- | --- | --- |
| all-case 128-token gate | failed | holds qualified performance acceptance |
| case 40 | diverged at token ID 7 without host restore | host fetch is not necessary for the observed instability |
| case 0 and case 51 repeats | full GPU-hit divergence observed | no same-output retry is justified as a fix |
| CPU audit suite | 120 passed, 0 failed, 8 ignored | prior 118-test receipt is stale; ignored GPU/package fixtures remain outside this receipt |
| actual-model transfer audits A and B | passed, both ranks/directions with page and row data, zero failures | transfer payload evidence only |
| B fixed-1 pilot | 52/52 equal IDs, 50 fetches | confirms the stated pilot scope; not fair timing evidence |

## Evidence processing limitations

The frozen normalizer rejects decode's declared `batch-before.prom` filename
as a numeric request index. Its original output and false decode phase gates
remain retained. A separately reviewed closure handler validates this exact
batch artifact and reports scoped functional proofs alongside the normalized
matrix. No request is discarded and numerical/performance gates remain failed.

For formal r1-A, the original readiness loop retained two spaced instant queries
but did not explicitly enforce an actual-scrape count. Historical Prometheus
queries at the nine recorded warmup starts and restoration epoch confirm three
healthy scrapes in45s and fresh host collection. This verifies actual recorded
state, not original enforcement. Raw empty polls remain retained; later windows
will enforce count/minimum-up/sample-age before traffic.

For r1-B, only 1/9 lanes meets the original strict monitoring proof: six lanes
have an empty first retained poll and two have post-traffic padding zeros.
Retrospective historical reads establish actual healthy pretraffic state for all
9/9 lanes, but do not rewrite the stricter original-loop outcome. Formal r2-B
has functional and monitoring proofs for 9/9 lanes. Its original restoration
proof remains false because its retained discovery receipt is empty; the later
matching live-generation capture at
`formal/r2-b/restoration-registration-refresh.json` is separate accepted
supplemental evidence at 54.9174 minutes; it does not rewrite the original
false proof.

## Recovery

The audit window restored and verified in 23.55 minutes on 2026-10-22. Formal r1-A then restored the exact retained incumbent identity; parent verification reached the second fresh monitoring snapshot at 55.71 minutes from window start. Formal r1-B reached its restoration observation at 53.0147 minutes, completed formal r2-B reached closeout at 49.736 minutes, formal r2-A restored at 50.8709167 minutes, formal r3-A restored at 51.0379667 minutes, and r3-B restored at 49.7004166683 minutes. r3-B parent review retained direct and routed seven-group checks including 20-tool batches and two accepted monitoring observations 45 seconds apart at 04:32:41Z; earlier failed polls remain retained. These are historical verified states, not proof of continuous or future health. No serve promotion occurred.

## Contribution readiness

Not ready. The candidate remains a local diagnostic change. Upstream is 132 commits ahead of the frozen source pin; no submission, commit, or promotion occurred. Upstream work requires a resolved numerical/transfer evidence scope and review; none is implied by these source/build tests.

## Next gates

1. Build an independent numerical component oracle and isolate the protected-lane regressions before any default decision.
2. Complete public-safe inventory, privacy, and size-policy review; then package the authorized report and retained evidence.
3. Retain numerical, fidelity, oracle, C4 mismatch, and diagnostic-only causal limits in every final publication artifact.

## Pending fields for final closeout

- v2 image digest and source-patch identity: **recorded**
- synthetic two-GPU 25%/75% audit: **passed; exact bytes, both ranks and directions**
- actual signed-model transfer audits A and B: **passed; byte transport only**
- B capacity: **158 rows / 2,434 pages; 525 protected plus warmup pages fit**
- B fixed-1 pilot: **52/52 equal IDs, 50 fetches; not fair timing**
- current audit-window restoration: **verified in 23.55 minutes**
- formal r1-A: **425/425 targets plus 27/27 warmups; nine fresh generations; independently verified restoration at 55.71 minutes**
- formal r1-B: **completed; restoration observation at 53.0147 minutes; original strict monitoring 1/9, retrospective actual-pretraffic monitoring 9/9**
- formal r2-B: **completed; 9/9 functional and monitoring proofs; closeout at 49.736 minutes; original restoration proof false, later refresh independently accepted at 54.9174 minutes**
- formal r2-A: **completed; 9/9 functional, 9/9 monitoring, and restoration proof true; restored at 50.8709167 minutes**
- formal r3-A: **completed; 9/9 functional, 9/9 monitoring, and restoration proof true; restored at 51.0379667 minutes**
- formal r3-B: **completed; 9/9 functional, 9/9 monitoring, and restoration proof true; restored at 49.7004166683 minutes**
- full formal matrix: **2,550 formal requests (1,152 prime, 1,398 measured), 162 warmups, 54 fresh incarnations, and no formal request failures**
- derived normalized, strict, and descriptive analyses: **recorded; fixed-work diagnostic causal support independently accepted; publication pending**
- composed CPU shared-fetch cancellation fixture: **passed and independently reviewed; separate later test-only delta**
- full AB, BA, AB diagnostic matrix: **completed; 2,550 formal requests, preserved failures and limits**
- complete AB, BA, AB metric table and failure ledger: **recorded in `README.md`, `summary.json`, and `analysis/`**
- independent causal review: **accepted for diagnostic fixed-work support only; human promotion decision remains not authorized**
