# Publication preparation — serving-performance follow-up

Status: preparation only. This is a private extraction plan, not a result, a
recommendation, or authorization for additional requests. Do not create a
public finding, summary, or matrix entry until the campaign has closed and the
owner has supplied restoration evidence.

## Pre-publication gate

`plans/comparison-plan.json` is currently a preparation snapshot:
`frozen_before_requests` is `false`. Before the first counted cell, the owner
must retain a replacement comparison plan with `frozen_before_requests: true`,
the exact current source revision, and content-bound source-file references.
It must preserve the two pinned arm identities and all required cells. The
native comparison envelope cannot claim complete coverage from the current
preparation snapshot.

The public follow-up bundle should use a new dated directory, leaving the
2026-10-17 FP4 A/B evidence immutable. Expected retained public-safe layout:

```
...-evidence/
  artifact-source.json
  artifact-manifest.json
  run-plan.json
  comparison-plan.json
  configuration.json
  workload-manifest.json
  coverage-and-gaps.md
  source-registry.json
  native/{baseline,candidate}/<cell>.json
  measurements/<cell>.json
  telemetry/<cell>-{before,during,after,delta}.json
  native/serving-comparison-envelope.json
  summary.json
  quality-ledger.json
  failures-and-friction.json
  restoration.json
```

Sanitize every copied value. Use synthetic host labels and redacted container,
GPU, port, path, and UUID identity. Preserve hashes only as artifact-integrity
claims with a transformation/provenance note where source identity is private.

## Native cell extraction

Each completed capacity output is expected to be
`anvil-serving.benchmark/v1`. Retain the original native schema and extract
only from its recorded fields:

- Identity and recipe: `model`, `engine`, `gpu`, `tier`, `manifest`,
  `source_recipe`, `configuration_fingerprint`, `measurement_protocol`,
  `serve_flags`, sampling, and `workload_identity`.
- Cell binding: `context_tokens`, `concurrency`, `requests`,
  `context_distribution`, `context_seed`, `max_context_tokens`, `max_tokens`,
  `response_words`, prompt-cache fields, and the arm identity from the frozen
  plan.
- Population/disposition: `completed`, `failed`, `responses_received`,
  `failures`, `performance_eligible`, and `metric_population`. A complete cell
  requires terminal request population equal to planned requests; failed or
  excluded requests remain retained.
- Client metrics: `metrics` and `request_timings`. Extract request-level
  distributions only from eligible requests: effective prefill, TTFO, visible
  TTFT, decode, TPOT, E2E, prompt/completion counts, and aggregate
  `throughput_tok_s`. Throughput remains a wall-clock scalar, not a p50/p95
  distribution. State sample counts and the native p99 interpretation.
- Client occupancy: `client_concurrency_observed_max` proves client stream-call
  overlap only. It does not prove engine execution or KV residency.

For every frozen cell write `measurements/<cell>.json`, a content-bound receipt:

```json
{
  "profile": "serving-comparison/v1",
  "arm": "baseline-or-candidate",
  "identity": "exact arm object from frozen plan",
  "cell": "exact required_cells entry",
  "raw": "content-bound reference to native/<arm>/<cell>.json"
}
```

The coverage cell references this receipt in `measurement`. Every `measured`
metric also references the receipt's raw native JSON. A wholly blocked or
unrun cell has `measurement: null`, no measured metrics, and a retained reason.

## Telemetry normalization

Collect and retain source snapshots, timestamped samples, sampling intervals,
and collector errors for each cell. The follow-up plan calls for engine samples
at roughly 0.5–1 s and GPU/cgroup/host samples at roughly 2 s; record actual
intervals rather than assuming that target cadence was achieved.

For each cell derive a separate telemetry table with content-bound source refs:

| Family | Required raw fields / derivation | Claim boundary |
|---|---|---|
| Engine phase histograms | before/after bucket, count, and sum; delta per prefill/decode/queue histogram when exposed | Delta is engine-reported telemetry; absent series is `not_collected`, never zero. |
| GPU | per-device used/free/utilization samples; start/end and peak/min summary | Sampling maxima/minima are lower-bound observations between samples. |
| KV and weights | engine-reported weights allocation, KV allocation bytes, and allocated token pool | Allocated token pool is a runtime allocation estimate, not proof of simultaneous full-context capacity. |
| Engine activity | active/queued requests and KV occupancy samples | Report observed/sample maximum separately from requested concurrency. |
| Host/cgroup | host available, cgroup current/peak/limit samples | Preserve accounting scope and collector errors. |
| Failure/admission | OOM, admission refusal, preemption, timeout, engine death, CUDA/NCCL errors | Keep raw event/log reference and classification; do not infer a physical limit from timeout alone. |

Telemetry values enter coverage as measured only when their source reference,
accounting scope, and collection interval are retained. Otherwise use
`not_collected`, `blocked`, or `estimated` with a reason and source.

## Comparison envelope and summary

Build one retained native `anvil-serving.benchmark-evidence/v1` envelope after
all authorized cells are terminal. Its `metrics_coverage` must:

1. reference the frozen serving-comparison plan;
2. have `required_cells` exactly equal to the plan keys;
3. include every frozen cell with a disposition and all required metric
   families; and
4. use `complete` only when every required metric in every cell is measured
   with the required sample population and provenance.

Use a hash-bound `comparison_evidence` reference from `summary.json` to that
native envelope. Copy the validated native `metrics_coverage` exactly into the
summary. Do not reformat, round, remove derived fields, replace a raw reference,
or recompute values in the summary; finalization requires exact equality,
including per-cell metric observations and measurement references.

A partial or stopped campaign remains publishable as a retained incomplete
bundle, but it is not a complete serving comparison. Keep the limitation and
missing-metric ledger explicit.

## Quality and decision ledger

Carry the previous A/B quality evidence as named, immutable prior evidence:
original context miss, authorized retry and isolated follow-ups, direct/text,
vision/OCR, synthetic agentic, and restoration. Do not pool retries into a
global failure rate; do not turn synthetic agentic evidence into repository
coding qualification. SWE remains unrun. This performance follow-up cannot
rewrite those results or promote either model. Record promotion as false unless
a separate human-approved promotion record exists.

## Closure checklist

- exact frozen plan and hashes retained before counted requests;
- every native capacity cell, measurement receipt, and telemetry source is
  present or explicitly blocked/unrun;
- coverage validates and summary coverage is byte-for-byte semantic equal to
  the validated native coverage;
- failures, collector gaps, and quality evidence remain visible;
- public transformation/privacy scan is clean;
- exact retained incumbent restoration has separate proof; and
- only then prepare the dated public finding and benchmark matrices.
