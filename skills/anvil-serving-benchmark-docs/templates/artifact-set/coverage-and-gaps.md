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
| OUTCOME | EXACT TEST OR EVIDENCE CONDITION | RELATIVE ARTIFACT LINK OR NONE | covered, partial, missing, rejected, or not-applicable | BOUNDED CLAIM OR NONE | NEXT GATE OR REASON |

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
