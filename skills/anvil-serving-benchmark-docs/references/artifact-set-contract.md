# Benchmark artifact-set contract

## Purpose

Every new benchmark campaign uses one campaign-level artifact set even when
the runner emits a modality-specific evidence schema. The artifact set makes
identity, inputs, raw results, failures, restoration, and the decision easy to
find without rewriting or flattening native evidence.

Copy the files in `templates/artifact-set/` into the dated finding's evidence
directory before the first live request. Keep their filenames stable:

```text
README.md
artifact-manifest.json
source-registry.json
summary.json
friction-log.md
restoration.json
campaign-state.json
dispatch-packet.md
coverage-and-gaps.md
```

Add `publication-summary.md` from `templates/publication-summary.md` when the
publication contract requires it. Corpus manifests, benchmark plans,
configuration snapshots, raw attempts, failure artifacts, generated media,
comparison tables, graph manifests, derived graph-data JSON, and SVG chart
packs remain additional artifacts. Graph data must retain each source artifact
path and SHA-256 so a chart can be traced back to native evidence.

`campaign-state.json`, `dispatch-packet.md`, and `coverage-and-gaps.md` are
compact working controls. They do not become benchmark proof merely because
they were copied. Retain them under `run-plan` or `failures-and-friction` only
when they are sanitized, final, and useful to reproduce or audit the campaign.
Otherwise leave them out of the final role ledger.

## Native evidence stays native

The campaign manifest wraps retained files; it does not translate one evidence
schema into another.

| Benchmark shape | Native evidence schema or contract |
|---|---|
| Durable context, agentic, and SWE jobs | `anvil-serving.benchmark-evidence/v1` |
| Capacity and throughput | `anvil-serving.benchmark/v1` |
| Image, video, and mixed-modality | `multimodal-benchmark-evidence/v1` |
| Bounded voice pipeline | `voice-benchmark-evidence/v1` |
| Multi-sample STT | `stt-benchmark-evidence/v1` |
| Anvil Media qualification | `anvil-serving.media-qualification/v1` |
| Kernel tuning | `kernel-tune-manifest/v1` plus paired native benchmark evidence |

Use the applicable product command to create native evidence atomically. Do
not hand-convert a failed or partial artifact into a successful common shape.

## Required artifact roles

`artifact-manifest.json` uses
`anvil-serving.benchmark-artifact-set/v1` and lists every role below exactly
once. A role may reference multiple retained files.

| Role | What it proves |
|---|---|
| `evidence-index` | Human-readable map of the bundle and its boundaries. |
| `source-registry` | Dated official, community, and local sources with evidence class and decision impact. |
| `workload-manifest` | Corpus, prompts, media, suites, hashes, licenses, and selection rules. |
| `run-plan` | Predeclared suites, controls, order, repetitions, context, concurrency, budgets, and gates. |
| `configuration-and-identity` | Repository, model, runtime, recipe, hardware, topology, endpoint, and dirty-state identity. |
| `raw-run-evidence` | Native request-level or aggregate outputs, including partial and failed attempts. |
| `failures-and-friction` | Actionable failures, workarounds, ambiguity, unsafe defaults, and repeated manual steps. |
| `restoration` | Starting state, ending state, differences, and independent post-run checks, or why restoration was not applicable. |
| `decision-summary` | Machine-readable bounded result, gates, limitations, evidence label, decision label, and promotion boundary. |
| `publication-summary` | Derivative public copy and claim ledger, or an explicit not-applicable reason. |

Each role has one of these statuses:

- `pending`: planned but not finalized; no final artifact set may retain it.
- `retained`: one or more relative files are present with SHA-256 and byte
  count.
- `not-applicable`: the role does not apply and `reason` says why.
- `missing`: expected evidence is unavailable and `reason` records the gap.

`missing` is valid negative evidence, but it blocks any qualification or
completion claim that depends on that role. Never use `not-applicable` to hide
a failed capture. All retained paths stay inside the evidence directory and
contain sanitized public data only.

Every item in a retained role's `files` array has exactly this shape:

```json
{
  "path": "relative/path/to/artifact.json",
  "sha256": "64-lowercase-hex-characters",
  "bytes": 123
}
```

Do not include `artifact-manifest.json` in its own role ledger, because that
would create a self-hash. Put its native schemas in `native_evidence_schemas`
and preserve the schema field inside every raw artifact.

Each `source-registry.json` entry records `id`, `url`, `published_or_observed`,
`observed_at`, `age_class`, `evidence_type`, `hardware_runtime_relevance`, and
`decision_impact`. Use an empty `sources` array when no external prior informed
the campaign; do not add a placeholder source.

## Request coverage and compact campaign state

Translate every material user request into one row of
`coverage-and-gaps.md` before measuring. Name the evidence condition that would
make the request covered, then link the retained artifact or leave the gap
explicit. A configured maximum, startup log, external result, or planned test
does not satisfy a measured request. Keep rejected, partial, and deferred rows
visible through publication.

Use `campaign-state.json` as a compact resume ledger. Update only stage state,
verified launcher identity, active assignments, completed cells, failures,
evidence paths, and next actions. It must not contain credentials, private
paths, raw prompts or responses, or copied logs. Use `dispatch-packet.md` for a
bounded delegated task; reference the state and native artifacts instead of
forking an entire long session when the agent platform supports a bounded or
no-history handoff.

## Creation workflow

1. Copy `templates/artifact-set/`, fill campaign identity and launcher state,
   and translate the request into `coverage-and-gaps.md` before live work.
   Leave planned roles and stages as `pending`.
2. Fill `source-registry.json` as sources are consulted. Record observed date,
   age class when time-sensitive, evidence type, hardware/runtime relevance,
   and decision impact.
3. Use explicit `research`, `feasibility`, `scout`, `finalist`, `quality`,
   `restoration`, and `publication` stages. A scout advances only after its
   identity, completion, correctness, and resource gates pass. A finalist uses
   the controlled comparison workload and adequate sample population. A
   performance winner is not qualified until the separately declared quality
   and integration gates pass or remain visibly missing.
4. Retain the native plan, workload/corpus manifest, exact configuration, and
   raw evidence without changing their schemas. Preserve partial and failed
   attempts.
5. Maintain `campaign-state.json`, `coverage-and-gaps.md`, and
   `friction-log.md` during the campaign rather than reconstructing them
   afterward. Every actionable failure records its immediate disposition,
   durable fix-forward artifact or explicit deferral, independent verification,
   and open/closed status. A successful manual retry is not a durable fix.
6. Complete `restoration.json` after the final post-run checks. Use
   `not-applicable` only when the campaign made no live mutation and name the
   read-only boundary.
7. Derive `summary.json` from retained artifacts. Do not copy headline values
   from prose or round incompatible statistics into agreement.
8. When comparable performance cells exist, render the graph manifest twice,
   verify byte-identical SVG output, and retain both the SVG and graph-data JSON
   under `raw-run-evidence`.
9. Finalize every manifest role, compute hashes and byte counts, then reconcile
   the evidence index, dated finding, result card, and publication summary.

The repository helper
`scripts/finalize_artifact_set.py` accepts an
`anvil-serving.benchmark-artifact-set-source/v1` file with the ten roles in the
order above and relative string paths. It fails closed on missing files, path
escapes, invalid roles/statuses, and self-hashing, then writes the canonical
hashed manifest. Keep the source file beside the bundle when repeatable
finalization is useful.

## Capacity comparison and replica aggregation

A repeatable capacity cell records the repository commit, the version and
sanitized source identity reached through `python -m anvil_serving.cli`, and
the complete workload controls. Comparative cells use a nonzero controlled-output
`response_words` target with sufficient `max_tokens` headroom. They explicitly
declare `prompt_cache_mode` as `unique` or `shared`; an omitted/default cache
state is not comparable evidence.

Finalist comparisons use `controlled_output_policy: strict`. Strict policy
requires a nonzero target and excludes a response unless the bounded capture
contains exactly the requested lowercase `code` words and no other output.
The default `observe` policy preserves command compatibility and records exact
adherence, extra words, and capture completeness without failing a non-exact
response. An `observe` result whose `exact_adherence` is false or null must not
be described as exact controlled decode. Validation captures are capped at
8,192 characters; a truncated capture is unobservable for output adherence and
fails strict policy.

Unique-cache concurrency cells require request canaries. A canary mismatch,
missing canary, marker that does not begin the response, peer/foreign
`ANVIL_REQ` marker, or truncated validation capture is a correctness failure
and excludes that request from a performance claim. Shared-cache cells remain
a separate population and record whether the measured request was cold or warm
plus any available cache-hit counters. Do not merge shared and unique
populations.

Speculative decoding is credited only against a matched no-speculation control
that keeps target identity, runtime, hardware/topology, context, concurrency,
KV/state formats, memory and batching controls, backends, graph capture,
parsers, cache state, seed, request count, controlled output, and canary policy
fixed. A comparison without this control may remain a scout result, but it is
not evidence of speculative speedup.

Treat p99 as meaningful only when its statistical population contains at least
100 measured requests. With fewer samples, preserve the computed value if the
native schema emits it, but label it descriptive and maximum-like rather than a
tail-latency estimate. At exactly 100 samples, nearest-rank p99 is one observed
tail order statistic and remains sensitive to a single request; do not call it
a stable service-level tail estimate without repetitions or a larger
population. Always publish the sample count and percentile method beside
percentiles.

In `capacity-v3`, TPOT and `mean_inter_token_latency_ms` are the same
per-request proxy: client-observed generation duration divided by completion
tokens after the first token. Percentiles summarize those per-request proxy
values. They are not percentiles over raw token-arrival intervals. Publish
token-level ITL only when the native artifact retains timestamped token events
and defines how transport chunking was converted to token arrivals.

To combine truly concurrent independent replicas, run
`scripts/combine_capacity_artifacts.py` over their complete, zero-failure
`anvil-serving.benchmark/v1` artifacts. New exact aggregates require identical
nonempty `clock_domain_id` values supplied with `--clock-domain-id`, matching
nanosecond timestamp fields, and identical
`configuration_fingerprint` values supplied as
`--configuration-fingerprint sha256:<64-lowercase-hex>`. Use a clock-domain
identifier only for processes on the same physical host clock; never infer a
common clock from host labels or reuse an identifier across machines. Inputs
also match the measurement protocol, engine, context/seed, serve flags,
controlled output, cache, and canary controls. A matching declared fingerprint
binds the inputs; it is not independent attestation of the running launch.

Legacy inputs without an explicit common clock remain acceptable only when
they record the same UTC start and finish second. The aggregate reports
second-precision alignment, lower and upper wall-time bounds, and lower and
upper throughput bounds; its compatibility `metrics.throughput_tok_s` is the
conservative lower bound. Missing configuration fingerprints produce
`replica_identity.status: legacy-unverified`, and missing modern canary or
controlled-output observations remain explicit validation limitations. These
fields preserve older evidence without retroactively claiming exact timing,
identity, or output adherence. Keep a stable input order, run the helper twice,
and require byte-identical `anvil-serving.capacity-aggregate/v1` output. Retain
the source artifacts and aggregate together: source paths and SHA-256 values in
the aggregate provide provenance, not permission to discard native evidence.

## Concise fail-fast verification and generated reports

Run one independently checkable command per step. A command that returns
nonzero ends that step; do not append hashing, graphing, finalization, or an
unbounded repository inventory after a failed native executable. Keep routine
output to status, bounded error detail, artifact path, digest, and verification
result. Use path-scoped `git status --short -- <owned-paths>` for review rather
than dumping an unrelated dirty worktree.

Before the first request, verify both `python -m anvil_serving.cli --version`
and `anvil_serving.cli.__file__`; retain only the version, commit, and sanitized
repository-relative module identity. Run campaign commands through
`python -m anvil_serving.cli` so a stale executable on `PATH` cannot select a
different checkout.

The generated recipe-results workflow is:

```text
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --confirm
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --check
```

The confirmed form writes the generated report. `--check` verifies the tracked
file is byte-current and performs no write. The implemented renderer binds
catalog identities and workload fields to native artifacts and reads every
displayed number through a declared numeric metric path. A documentation claim
still requires a successful command and current generated output.

## Completion gate

A campaign artifact set is structurally complete only when:

- all ten roles appear exactly once and none remains `pending`;
- every `retained` file exists, is relative to the bundle, and matches its
  recorded SHA-256 and byte count;
- the native evidence schema, completion state, and failures are preserved;
- `summary.json` and public claims reconcile with raw evidence;
- restoration is verified or explicitly not applicable; and
- `promotion_authorized` remains `false` unless a separate human-gated
  promotion record says otherwise.

Artifact-set completion is documentation and evidence hygiene. It never
authorizes a serve, route, client catalog, or deployment change.
