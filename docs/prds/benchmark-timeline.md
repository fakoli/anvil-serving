# Project: Correlated benchmark timeline

## Summary

Extend the existing observability dashboard with a retained, metadata-only
benchmark timeline. Join measured job phases, native suite requests and tool
fixtures, and available resource samples by explicit run/session identity.
Operators should be able to see where a benchmark spent time and where evidence
is missing without opening raw transcripts or inferring causation from a chart.

## Status and evidence

**Status:** Proposed. **Priority:** Medium-high.
**Baseline:** Anvil `f50dca489780b95d0cd98dee59cf620618c4ccd1`.

Anvil already provides a packaged dashboard, bounded telemetry retention,
timestamp-aware session comparisons, benchmark capture phases, and durable
benchmark jobs. This is an integration and drill-down project, not a replacement
dashboard. `CaptureSession` already separates benchmark time from post-run
capture. The existing overhead gate requires zero benchmark-capture disk writes
and zero GPU allocation: preserve that requirement.

Miles' [dashboard](https://github.com/radixark/miles/blob/d2fc97ce581577e255e494801d7568747d5a10d7/miles/dashboard/README.md)
demonstrates phase/resource/trajectory correlation. Borrow that presentation and
explicit gap reporting; do not import its training-specific collectors or display
conversation content. [Issue 379](https://github.com/fakoli/anvil-serving/issues/379)
owns fleet environment stamps. Consume a stamp if available; absence must not
block this feature or trigger another fleet scan.

## Goals

- Distinguish preparation, preflight, request execution, grading, and finalization.
- Correlate individual native requests with retained telemetry where identity permits.
- Make missing timestamps, dropped events, failed capture, and partial runs visible.
- Keep collection bounded, opt-in, engine-agnostic, and out of inference response handling.

## Non-Goals

- Automatic bottleneck diagnosis, model promotion, remediation, or route changes.
- A new telemetry polling loop, faster GPU sampling, or a tracing backend dependency.
- Invented server prefill/decode/queue timings derived from total client latency.
- Raw prompts, responses, tool arguments/results, reasoning text, or log viewing.
- Live streaming of the new timeline; v1 renders finalized retained artifacts.
- Replacing existing dashboard graphs, job evidence, capture artifacts, or ownership registries.

## Requirements

- R001: Add an opt-in `capture_timeline` boolean to the existing durable job
  parameter contract, default false. Expose it consistently as `--capture-timeline`
  on existing context/agentic/SWE job start commands and as an optional boolean
  in the corresponding MCP input. Preview must show the selected setting.
- R002: Every event belongs to one `run_id` and `timeline_id`, with a monotonically
  increasing run-local sequence. Parent span IDs refer only to the same timeline.
  A telemetry `session_id` is an explicit optional join, never guessed from time.
- R003: Measure durations with one process-local monotonic clock. Store UTC wall
  timestamps for display plus a clock-domain ID. Never subtract monotonic values
  from different processes/hosts. Imported resource samples retain their own
  source timestamp, observed timestamp, and freshness/gap indicators.
- R004: Instrument existing worker stages and native suite call boundaries.
  Emit model-request spans and deterministic tool-fixture spans only where
  measured. An opaque SWE harness gets a harness span, not fabricated per-turn
  timings. Future Harbor support may contribute spans through the same sink.
- R005: Event payloads follow a closed allowlist. Optional usage counts and
  request IDs are retained only when observed and validated. Missing TTFT,
  token usage, server timing, or host association is null/unavailable, never zero.
- R006: Collection performs no additional network calls, Docker calls, model
  calls, GPU allocations, or timeline disk writes during the measured workload.
  Use a bounded in-memory buffer with nonblocking insertion. Overflow drops new
  events, increments a counter, and marks capture partial; inference continues.
- R007: Capture errors cannot change suite scoring, retry a request, or hide a
  job failure. Record timeline quality separately from benchmark completeness.
  Graceful cancellation finalizes available events after workload stop; a killed
  worker may have no timeline and must be reported as unavailable.
- R008: Persist a versioned sidecar and manifest atomically after measured
  execution/capture ends, under the existing owned run root. Register their
  relative paths and hashes through existing evidence-reference machinery.
  Never rewrite historical evidence or mutate an existing profile hash.
- R009: A read-only authenticated dashboard endpoint accepts an owned run ID
  and bounded pagination, not a filesystem path or URL. Resolve through the
  configured job store, verify ownership and final artifact hashes, and reject
  traversal, symlinks/reparse escapes, unknown schema, and oversized input.
- R010: Render phase lanes, native request/fixture spans, and existing resource
  curves on a shared elapsed-time view with textual alternatives. Show capture
  quality, dropped-event count, gaps, and unavailable associations prominently.
- R011: Keep resource lanes separate by host/device identity. Do not sum device
  VRAM as unified memory, infer GPU ownership from utilization, or label temporal
  overlap as causal attribution. Host labels in public exports must be sanitized.
- R012: Timeline-disabled jobs retain existing behavior. Capture-enabled jobs
  must meet the existing observability overhead contract before broader enablement;
  deterministic fixtures alone are not measured overhead evidence.

## Proposed data contract

Put the new serializer/validator in
`anvil_serving/observability/benchmark/timeline.py` (proposed). Use
`anvil-serving.benchmark-timeline/v1` for the manifest and event schema. A closed
manifest contains schema, run/timeline IDs, optional telemetry session ID,
start UTC, clock-domain ID, event count, dropped count, capture quality
(`complete`, `partial`, `unavailable`), bounded reason codes, and the event file's
relative path, byte length, and SHA-256. Do not include unrestricted metadata.

Event fields:

| Field | Contract |
| --- | --- |
| `seq` | Positive increasing integer; gaps allowed after dropped events |
| `span_id`, `parent_span_id` | Opaque bounded IDs; parent nullable |
| `kind` | `phase_start`, `phase_end`, `request_start`, `request_end`, `fixture_start`, `fixture_end` |
| `name` | Closed stage names or validated synthetic case IDs, not arbitrary tool text |
| `elapsed_ns`, `wall_time_utc`, `clock_domain_id` | Observed timing; elapsed is nonnegative and local to the producer |
| `status` | Null for start; `completed`, `failed`, or `cancelled` for end |
| `request_id`, `case_id`, `attempt` | Optional validated correlation fields; no payloads |
| `prompt_tokens`, `completion_tokens` | Optional observed nonnegative integers, never inferred |
| `failure_code` | Optional code from a closed mapping, never exception text |

Run/timeline IDs may be stored in the manifest rather than repeated in every
JSONL row, provided the reader binds every row to that manifest. An unmatched
start is an incomplete span with unknown end, not a span ending at artifact
finalization. An unmatched end or parent in a capture explicitly marked partial
is a visible gap with unknown duration; the same condition in a supposedly
complete capture is an integrity error. Never invent the missing boundary.
Do not add per-token events.

Centralize proposed bounds: 10,000 events, 2 KiB maximum serialized event, 8 MiB
total serialized event budget, and a measured in-memory cap compatible with the
existing capture RSS limit. Enforce both event and byte limits; bound the final
manifest to 16 KiB. Store primitive validated records to avoid retaining large
request objects by reference. A stopped/full sink rejects new events immediately.

The new API is proposed as
`GET /v1/benchmark-timeline?run_id=<id>&after_seq=0&limit=500`.
Allow at most 1,000 events and 1 MiB per response, returning a next sequence
cursor when needed. Unknown/duplicate query keys and invalid limits are errors.
An owned run with no timeline returns a typed unavailable result; an unknown run
must not disclose filesystem state. Reuse current dashboard error/status
conventions rather than adding a second server stack.

## Implementation map

| Existing file | Reuse/extend |
| --- | --- |
| `anvil_serving/benchmarking/worker.py` | Stage boundaries, suite invocation, cancellation/final evidence |
| `anvil_serving/benchmarking/suite_runner.py` | Native request and deterministic fixture boundaries |
| `anvil_serving/benchmarking/jobs.py` | Closed job parameters, canonical spec identity, owned paths |
| `anvil_serving/benchmarking/jobs_cli.py` | Shared start arguments |
| `anvil_serving/control_plane/mcp/tools/benchmarks.py` | Matching job input schema and preview |
| `anvil_serving/benchmarking/artifacts.py` | Atomic persistence and evidence references |
| `anvil_serving/observability/benchmark/session.py` | Existing capture lifecycle and phase timing |
| `anvil_serving/observability/benchmark/artifact.py` | Telemetry artifact identity, redaction, finalization |
| `anvil_serving/observability/benchmark/overhead.py` | Existing measured resource/benchmark-effect gates |
| `anvil_serving/observability/dashboard/app.py` | Existing authenticated server and query routes |
| `anvil_serving/observability/dashboard/history.py` | Retained session registry and identity-safe joining |
| `anvil_serving/observability/dashboard/timeseries.py` | Resource curves and explicit gaps |
| `anvil_serving/observability/dashboard/static/index.html` | Packaged vanilla dashboard; no frontend framework |

Implementation order: standalone sink/validator, worker hooks, owned artifact
reader, then UI. Pass an optional no-op-compatible sink into native runners;
do not make the router import the dashboard or instrument all production traffic.
When telemetry capture is not configured, render phases/requests alone and label
resource correlation unavailable. When configured, reuse its existing lifecycle
and retention cadence; don't start a second sampler or extend benchmark runtime
to wait for a chart.

## Features

### F001: Bounded measured timeline and durable artifact

**Requirements:** R001, R002, R003, R004, R005, R006, R007, R008

### F002: Secure retained timeline API and dashboard view

**Requirements:** R009, R010, R011

### F003: Compatibility and measured overhead acceptance

**Requirements:** R012

## Tasks

### T001: Implement and test the in-memory event contract

**Feature:** F001
**Priority:** high
**Likely files:** proposed timeline.py and tests/observability/test_benchmark_timeline.py

Inject monotonic and UTC clocks. Validate records before retaining them; reject
unknown fields and non-finite/negative numbers. Keep overflow accounting separate
from the event buffer so a full buffer cannot hide its own quality warning.

**Acceptance criteria:**

- Exact limits, overflow, clock regression, invalid parents, unmatched spans,
  and unknown fields have deterministic tests.
- Synthetic secret/prompt markers never survive in an event or manifest.
- Slow/failing finalization cannot make event insertion block on disk I/O.
- A full buffer produces partial quality and an accurate dropped count.

**Verification:**

- After creating the test: `python scripts/run_tests.py tests/observability/test_benchmark_timeline.py -x -q`

### T002: Wire opt-in job capture and finalization

**Feature:** F001
**Priority:** high
**Dependencies:** T001
**Likely files:** benchmarking/worker.py, suite_runner.py, jobs.py, jobs_cli.py,
control_plane/mcp/tools/benchmarks.py and corresponding existing tests

Add the boolean to every strict job input surface. Emit events around existing
operations using `try/finally`; preserve original exceptions and scores. Flush
only after the measured window ends. Attach references using the existing
evidence format's supported extension/reference seam, not extra unvalidated keys.

**Acceptance criteria:**

- Disabled jobs produce unchanged suite behavior; old specs still validate.
- Native model requests and fixture work have distinct measured spans.
- Failed/cancelled jobs retain honest partial capture; no request is retried.
- An opaque external harness shows only measured enclosing spans.
- Timeline persistence failure preserves the benchmark result and records a
  bounded separate capture diagnostic in job metadata/evidence where possible.

**Verification:**

- `python scripts/run_tests.py tests/test_benchmark_jobs.py tests/test_benchmark_worker.py tests/test_benchmark_suite_runner.py tests/control_plane/test_benchmark_jobs.py -x -q`
- Add CLI/MCP schema and preview tests for `capture_timeline` using current manifest tooling.

### T003: Add a bounded owned-run artifact reader and API

**Feature:** F002
**Priority:** high
**Dependencies:** T002
**Likely files:** observability/dashboard/app.py, history.py,
tests/observability/test_dashboard.py, proposed timeline reader tests

Resolve artifacts from a configured job-store/run-root adapter injected into the
dashboard. Do not scan arbitrary directories or accept a client-supplied path.
Verify the finalized manifest and JSONL bytes before rendering. Old runs remain
listable with timeline unavailable; a corrupt new artifact gets an explicit error.

**Acceptance criteria:**

- Auth protection matches existing private JSON routes.
- Run A cannot select an artifact owned by run B.
- Traversal, escaped symlink/reparse point, bad hash, unknown schema, oversized
  line, duplicate sequence, and hostile query parameters are rejected.
- Pagination is stable and never exceeds row/byte bounds.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_dashboard.py tests/observability/test_benchmark_timeline.py tests/test_benchmark_artifact_integrity.py -x -q`

### T004: Render retained spans with resource gaps and accessible detail

**Feature:** F002
**Priority:** medium
**Dependencies:** T003
**Likely files:** observability/dashboard/static/index.html,
tests/observability/test_milestone3_dashboard.py, new synthetic timeline fixtures

Add a run selector and timeline panel to the existing shell. Support a
keyboard-selectable span and a textual table with start, duration, status, and
observed counts. Draw missing intervals explicitly. Use textContent/escaped
text, never raw artifact HTML. Show a clear empty state for historical runs.

**Acceptance criteria:**

- Complete, cancelled, overflowed, no-telemetry, and clock-misaligned fixtures
  are distinguishable without interpreting color alone.
- Resource lanes preserve host/device separation and show their time uncertainty.
- No raw transcript, hidden prompt, or unrestricted metadata appears in API/UI.
- Inspect the rendered page at desktop and narrow widths with synthetic data.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_milestone3_dashboard.py tests/observability/test_dashboard.py -x -q`
- Record browser inspection of the listed fixture states; HTML string assertions alone are insufficient.

### T005: Measure overhead and document evidence limits

**Feature:** F003
**Priority:** high
**Dependencies:** T004
**Likely files:** observability/benchmark/overhead.py (reuse, do not relax),
tests/observability/, docs/benchmarks/context-agentic-swe.md

Run collector-off/on measurements on an isolated synthetic workload first and
record process/subprocess resources. Live model A/B requires separate explicit
benchmark authority. Keep the feature opt-in and label live overhead unqualified
until that measured gate is completed.

**Acceptance criteria:**

- Existing CPU/RSS, zero GPU allocation, zero capture disk-write, and 1% workload
  effect limits are preserved; noisy/insufficient evidence is inconclusive.
- A deliberately blocking or disk-writing sink fails an independent negative control.
- The docs distinguish source/fixture completion from measured live acceptance.
- Existing dashboard/capture behavior and historical artifact reading pass regressions.

**Verification:**

- `python scripts/run_tests.py tests/observability/ tests/test_benchmark_worker.py -x -q`
- `python -m mkdocs build --strict`
- `git diff --check`

## Acceptance Criteria

From one retained synthetic run, an operator can identify preparation, request,
fixture, and grading time; inspect a failed span; see missing resource evidence;
and distinguish an incomplete capture from a failed benchmark. No resource
correlation may rely on matching timestamps alone. Overhead claims require
recorded measurements, not merely successful UI or schema tests.

## Risks

- Writing events during the benchmark would violate the current capture contract.
- Unbounded Python object retention can exceed limits despite a small JSON file.
- Cross-host clock skew can make an apparently precise chart misleading.
- Capturing all request metadata can leak content; use a closed schema from day one.
- Worker kill cannot guarantee a final artifact when capture is memory-only.

## Assumptions

### A001: Retained post-run analysis delivers useful v1 value.

**Rationale:** It avoids a new streaming/persistence path in the measured workload.
**Requirements:** R006, R008, R009, R010

### A002: Optional resource evidence must not block benchmark execution.

**Rationale:** The existing capture lifecycle and environment stamp are advisory evidence.
**Requirements:** R007, R010, R011

## Open Questions

- Parked: live event-tail UI, distributed clock synchronization, and detailed
  external-harness step timing. Do not simulate these capabilities in v1.
- Parked: enabling capture by default after representative measured acceptance.

## Rollout and rollback

Ship disabled by default and verify on isolated fixture runs. Disabling the
option stops new collection without deleting evidence. Existing dashboard
versions ignore the separately referenced timeline sidecar; existing benchmark
scores and profiles remain valid. Service deployment and live A/B execution are
separate operator actions, not part of writing or approving this PRD.
