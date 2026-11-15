# Request-to-evidence coverage

Update this matrix as requests arrive and as gates finish. A plan, configured
limit, healthy endpoint, or external claim does not make a row covered; link
the retained artifact that proves the requested outcome.

For a serving comparison, retain a content-bound `frozen_plan` before requests.
The `serving-comparison/v1` plan has `frozen_before_requests: true`, pins source
revision/file hashes, at least two model/revision/image/runtime/hardware arms,
and maps every required cell to arm, workload, context, concurrency, and request
count. `metrics_coverage.required_cells` must exactly match plan cells. A cell
with measured metrics needs a measurement receipt that binds exact arm identity,
configuration, workload, and request population to native JSON; every measured
metric references that native artifact. A wholly blocked or unrun cell can use a
null measurement only when it has no measured metrics and retains a reasoned
disposition. Validation checks this provenance binding and does not recompute
metric statistics. For each required metric use one state:
`measured`, `configured`, `estimated`, `blocked`, or `not_collected`. Only
retained observations are measured. Engine-reported `kv_cache_tokens` are
allocated slots; a full-context concurrency calculation from them is a separately
labeled estimate, not observed occupancy or simultaneous capacity.

| Requested outcome | Required gate | Retained evidence | Status | Claim allowed | Gap or next action |
|---|---|---|---|---|---|
| Single-GPU MTP benefit | Matched no-spec control, retained metrics | [comparison](raw/r4-comparison-decision.json) | partial | MTP improved historical ordinary-summary C1/C2 decode under retained conditions | Strict controlled output failed; do not generalize to exact decode |
| Highest usable concurrency | Concurrent direct runs and latency boundary | [fresh capacity summary](raw/capacity-summary.json) · [resource validation](raw/resource-final-validation.json) | covered for tested fresh cells | C2 at 32K: decode p50 13.43 tokens/s, first-output p95 16.69s, within 30s bound | Decode is a soft miss versus 15 tokens/s objective; C16 queues beyond effective active cap 9 |
| Latency-first operation | Fresh direct C1 capacity cells | [fresh capacity summary](raw/capacity-summary.json) | covered for tested fresh cells | C1 decode p50 63.44 tokens/s at 8K and 62.46 tokens/s at 32K | Separate 10-cell fresh population; do not pool with historical comparison |
| Image operation | Direct deterministic image checks | [image evidence](raw/images.json) | covered for tested images | 12/12 images and 2/2 ordered eight-image probes passed | Video untested and excluded |
| Agentic operation | Native full suite | [agentic evidence](raw/agentic-native-evidence.json) | covered for suite | 18/18 passed | Coding and context gates remain separate |
| Coding quality | Independent frozen SWE suite | [native SWE evidence](raw/swe-native/native-evidence.json) · [minimal official grader report binding](raw/swe-native/official-grader-report-binding.json) · [validator](raw/swe-native-validation.json) | covered for fixed campaign floor | 4/5 resolved after independent grading; all five agent tasks submitted | Django10097 remained unresolved; this five-task floor is not a broad population estimate |
| Long context C1 | All cases pass, actual input coverage ≥99%, allowed finish | [native context](raw/context-native/native-evidence.json) · [admission](raw/context-admission.json) | covered | 36/36 passed; near-full actual input 245596–245626, reserve 16384 within configured context | Timing overlapped coding work and is not speed evidence |
| Long context C2 | Sealed concurrent pair with distinct long user-content prefixes | [concurrent result](raw/context-concurrent/result.json) · [request evidence](raw/context-concurrent/requests.jsonl) · [amendment](raw/c2-evidence-amendment.json) | covered for observed pair | 2/2 exact-answer requests overlapped for 260.115s; sampled engine state recorded running=2 and peak used KV 458520 | Actual prompt plus reserved output tokens were 491236; do not infer hypothetical full-residency capacity from allocator samples |
| Stability serial | Three unique-prefix serial rounds | [serial stability](raw/stability-serial-unique.json) | covered for diagnostic scenario | 3/3 retrieval/runtime/coverage passed | Native stability evidence is diagnostic and performance-ineligible |
| Stability overlap | Unique- and repeat-prefix anchor/contender overlap coverage | [immutable incomplete overlap](raw/stability-overlap-unique.json) · [unique successor](raw/stability-overlap-unique-r2.json) · [repeat successor](raw/stability-overlap-repeat-r2.json) · [validator](raw/stability-final-validation.json) · [initial review](raw/independent-stability-final-review.json) · [source-binding correction](raw/independent-stability-source-binding-correction.json) | covered for diagnostic scenarios | Successor unique 3/3 and repeat 3/3 passed under `contender-stream-active/v1`; correction accepts the exact source-digest method | Old observer failure remains visible. Successor is diagnostic-only: no scheduler proof or performance ranking |
| Resource envelope | Required reserve and no OOM | [r5 rejection](raw/mtp3-r5-rejection.json) | partial | R5 .94 is policy-infeasible; r4 .90 retained | Final r4 window must close cleanly |
| Fresh final resource envelope | Continuous resource observer plus deep health | [resource validation](raw/resource-final-validation.json) · [deep health](raw/deep-health.json) | covered | 11949 engine and 2992 slow samples; 0 OOM events, 0 identity changes, GPU1 memory max 0 MiB | Sampling is a bounded campaign window, not an indefinite service guarantee |
| Promotion | Human-gated serve and route result | none | missing | none | Promotion remains unauthorized |

## Serving-comparison metric matrix

| Profile | Workload and recipe identity | Context | Requested concurrency | Required metric | State | Measurement receipt and evidence/disposition |
|---|---|---:|---:|---|---|---|
| PROFILE | WORKLOAD_SHA / RECIPE_IDENTITY | TOKENS | REQUESTS | effective prefill; TTFO/visible TTFT when separately observable; decode; TPOT; E2E; aggregate output throughput; actual input/output counts and distributions; memory/weights/KV bytes and runtime token pool; observed occupancy; GPU/host/cgroup memory; OOM/admission; sample count/p50/p95 | measured, configured, estimated, blocked, or not_collected | CONTENT-BOUND RECEIPT (EXACT ARM/CELL + NATIVE JSON) AND ARTIFACT OR REASON |

Keep unsuccessful and explicitly deferred requests visible. Distinguish a
configured or advertised maximum from an actually validated request, and a
single scout from repeated finalist or quality evidence. A focused smoke or
quality-only check may scope performance out and explain why it was not
remeasured. It is not a full serving comparison. A full comparison is complete
only when every required metric in every frozen cell is measured.
