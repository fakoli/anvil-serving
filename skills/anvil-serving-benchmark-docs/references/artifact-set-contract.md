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
```

Add `publication-summary.md` from `templates/publication-summary.md` when the
publication contract requires it. Corpus manifests, benchmark plans,
configuration snapshots, raw attempts, failure artifacts, generated media,
and comparison tables remain additional native artifacts.

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

## Creation workflow

1. Copy `templates/artifact-set/` and fill the campaign identity before live
   work. Leave planned roles as `pending`.
2. Fill `source-registry.json` as sources are consulted. Record observed date,
   age class when time-sensitive, evidence type, hardware/runtime relevance,
   and decision impact.
3. Retain the native plan, workload/corpus manifest, exact configuration, and
   raw evidence without changing their schemas. Preserve partial and failed
   attempts.
4. Maintain `friction-log.md` during the campaign rather than reconstructing
   it afterward.
5. Complete `restoration.json` after the final post-run checks. Use
   `not-applicable` only when the campaign made no live mutation and name the
   read-only boundary.
6. Derive `summary.json` from retained artifacts. Do not copy headline values
   from prose or round incompatible statistics into agreement.
7. Finalize every manifest role, compute hashes and byte counts, then reconcile
   the evidence index, dated finding, result card, and publication summary.

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
