# Benchmark results

Every dated finding is published as one **benchmark record** with the same
fields: what was measured, on which model, engine, quantization, and hardware,
under which context and concurrency, what was decided, and which values the
retained evidence does not record. <!-- benchmark-records:summary -->

Records are derived views. The dated finding and its retained artifacts remain
the evidence; a record never promotes a model or changes a route.

## Throughput and interactivity

Each point is one retained capacity measurement. Lines connect one
configuration's concurrency sweep at a fixed context. Moving right means faster
responses for each user; moving up means more total output from the server.
Select a point to open its finding.

<div id="benchmark-explorer" markdown="0">
  <p>
    <label>Finding <select data-filter="record"><option value="">All findings</option></select></label>
    <label>Hardware <select data-filter="hardware"><option value="">All hardware</option></select></label>
    <label>Context <select data-filter="context"><option value="">All contexts</option></select></label>
    <label><input type="checkbox" data-filter="eligible" checked> Exclude runs marked not performance-eligible</label>
  </p>
  <p data-status>Loading the benchmark index…</p>
  <div id="benchmark-tradeoff" style="width: 100%"></div>
  <div id="benchmark-ttft" style="width: 100%"></div>
</div>

<!-- benchmark-records:scripts -->

!!! warning "Compare like with like"
    Throughput and latency move with context length, output length, concurrency,
    cache state, and measurement path. Filter to one finding or one context
    before comparing points, and read the finding's caveats before quoting a
    number. Points from different findings were not measured as matched pairs.

## All records

Newest first. *Gaps* lists core fields the retained evidence does not record;
they are shown rather than inferred. <!-- benchmark-records:download -->

<!-- benchmark-records:table -->

## The record format

Each record has the schema `anvil-serving.benchmark-record/v1`.

| Group | Fields | Source |
|---|---|---|
| Identity | `id`, `date`, `title` | Finding file name and title |
| Evidence | `categories` (capacity, quality, functional, diagnostic, multimodal, external-prior), `evidence_labels` | Artifact-set manifest, decision summary, recognized artifacts |
| Decision | `decision.labels`, `decision.promotion_authorized` | Manifest, decision summary, or the finding's **Decision:** line |
| Subject | `model`, `engine`, `quantization`, `hardware` | Decision summary `identity`, else values observed in retained artifacts |
| Workload | `context_tokens`, `concurrency` | Retained artifacts |
| Results | `headline`, `failures`, `limitations` | Decision summary |
| Artifacts | counts by kind, artifact-set role status | Evidence bundle |
| Links | finding, evidence index, manifest, summary | Evidence bundle |
| Gaps | core fields that are `not recorded` | Computed |

The index also carries `capacity_points` (one row per capacity artifact, with
per-user decode p50, aggregate output, TTFT p50/p95, E2E p50, and completion
counts) and `quality_suites` (one row per suite with item and attempt pass
counts).

### Make a new finding complete

Records fill themselves from the
[artifact set](finding-format.md#campaign-artifact-set-v1). To close the common
gaps, record these keys in the campaign's `summary.json` `identity` block:
`model`, `engine`, `quantization`, and `hardware`. Then preview the record:

```bash
anvil-serving eval benchmark records --record 2026-11-03-rtx5090-campaign
```
