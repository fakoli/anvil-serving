---
name: anvil-serving-benchmark-docs
description: Publish and maintain hardware-first, publication-ready benchmark documentation in anvil-serving. Use whenever a model is newly configured, served, benchmarked, qualified, promoted, rolled back, or fails meaningfully, including LLM, vision, Omni, STT, and TTS results. Separates measured hardware from protected/co-resident topology and local evidence from research priors; never authorizes a serve or route change.
---

# Anvil Serving Benchmark Docs

Use this skill for the publication phase of every meaningful model run. Read
`references/artifact-set-contract.md`, `references/publication-contract.md`,
`README.md`, `CLAUDE.md`,
`docs/benchmarks/index.md`, and the applicable model or modality skill before
editing.

For a multi-cell, delegated, or resumable campaign, also read
`docs/benchmarks/repeatable-campaigns.md` before the first live request.

## 1. Inspect exact evidence

1. Inspect the retained artifact with
   `anvil-serving eval benchmark evidence show ARTIFACT --json` or the
   applicable modality skill. Do not infer a pass from a command transcript.
2. Record model repository and exact revision, served name, image digest and
   engine revision, quantization, KV format, context, concurrency, host,
   measured GPU, co-resident/protected hardware, workload, completion state,
   failures, and caveats.
3. Classify local evidence as `compatibility-only`, `functional`, `capacity`,
   `quality`, or `historical-invalid`. Classify research as `external-prior`.
4. Choose a separate decision label: `current`, `rollback`, `challenger`,
   `no-promotion`, or `rejected`.
5. Stop rather than converting missing identity, failed loads, reasoning
   exhaustion, partial artifacts, or topology observations into qualification.

## 2. Create the common artifact set

For every new campaign, copy `templates/artifact-set/` into the dated finding's
evidence directory before the first live request. The six common files provide
the human index, `anvil-serving.benchmark-artifact-set/v1` role ledger, source
registry, decision summary, friction log, and restoration record.
Use the included `campaign-state.json`, `dispatch-packet.md`, and
`coverage-and-gaps.md` working controls to keep stage state, delegated scope,
and every requested outcome compact and explicit. They are not evidence unless
the final role ledger deliberately retains them.

Keep the applicable native evidence schema. Do not convert capacity,
multimodal, voice, STT, media, kernel, context, agentic, or SWE artifacts into
one lossy result shape. Instead, map their retained files to all ten semantic
roles in `references/artifact-set-contract.md`. Every final role is
`retained`, `not-applicable`, or `missing`; no final set may retain `pending`.
Use `missing` for an evidence gap and `not-applicable` only with a reason.

Fill the source registry and friction log during the campaign. Capture the
starting state before mutation, complete restoration after post-run checks,
and derive `summary.json` only from retained evidence. For recognized JSON
artifacts, use `anvil-serving eval benchmark evidence show ARTIFACT --json` to
normalize inspection without rewriting the source artifact.

For a bundle with many files, keep a reviewed
`artifact-manifest-source.json` beside the artifacts, then finalize exact
SHA-256 and byte counts with:

```text
python skills/anvil-serving-benchmark-docs/scripts/finalize_artifact_set.py PATH/artifact-manifest-source.json
```

Run the finalizer twice and require byte-identical output. The source is a
reproducibility input, not a substitute for the canonical
`artifact-manifest.json`.

### Capacity-cell repeatability

Before the first capacity request, prove that the product launcher resolves to
the intended worktree. Run `python -m anvil_serving.cli --version`, inspect
`anvil_serving.cli.__file__`, and use `python -m anvil_serving.cli` for every
campaign command. Retain the version, repository commit, and a sanitized
repo-relative source identity; never publish a personal absolute path. Do not
assume that an `anvil-serving` executable found on `PATH` imports the worktree.

Predeclare a controlled-output workload for comparative cells. Set a nonzero
`--response-words` target and enough `--max-tokens` headroom for it to finish,
then keep both values fixed across the comparison. Use
`--controlled-output-policy strict` for finalist comparisons: a non-exact or
capture-truncated completion becomes a failed, performance-ineligible request.
The default `observe` policy records requested words, observed `code` words,
extra words, capture completeness, and exact adherence without rejecting the
request; it is appropriate for scouts and compatibility with older commands,
but non-exact or unobservable output is not exact controlled-decode evidence.
Visible output retained for validation is capped at 8,192 characters, so choose
a target that can be captured as well as completed. Short, EOS-terminated scout
runs may find candidates, but they are not substitutes for controlled decode
measurements. Use at least 100 measured requests in each statistical population
before treating p99 as meaningful; below 100, retain p99 only as a descriptive,
maximum-like statistic and label the sample count prominently.

Set `--prompt-cache-mode unique` or `--prompt-cache-mode shared` explicitly for
every capacity cell and verify the artifact's `prompt_cache_mode` field.
Unique-cache cells also use `--request-canaries`; every
response must begin with its own marker, contain no other `ANVIL_REQ` marker,
and have a complete validation capture before its latency or throughput can
enter a comparison. Shared-cache cells are deliberate cache-reuse experiments
and must record cold/warm state and available hit counters. Never pool or graph
shared and unique cache populations as one series.

Every speculative-decoding cell needs an otherwise matched no-speculation
control. Hold model and revision, runtime image and engine revision, hardware,
TP/DP topology, context, concurrency, KV/state dtype, memory fraction,
attention and linear backends, batching, graph capture, parsers, cache mode,
request count, controlled output, seed, and canary policy fixed. Only the
speculative configuration and unavoidable endpoint/evidence labels may differ.

For synchronized independent replicas, retain each native
`anvil-serving.benchmark/v1` artifact and derive one aggregate with:

```text
python skills/anvil-serving-benchmark-docs/scripts/combine_capacity_artifacts.py REPLICA_A.json REPLICA_B.json --output COMBINED.json
```

Inputs must be complete, failure-free, performance-eligible, and match the
measurement protocol, engine, context/seed, serve flags, controlled workload,
cache mode, and canary policy. For a new exact aggregate, launch every replica
with the same public-safe `--clock-domain-id` only when the processes use the
same physical host clock, and the same
`--configuration-fingerprint sha256:<64-lowercase-hex>` for the normalized
replica configuration. Never reuse a clock-domain identifier across hosts.
Matching nanosecond timestamps without that explicit common clock declaration
do not prove alignment, and a missing fingerprint leaves replica identity
unverified. A matching declared fingerprint binds the inputs for comparison;
it is not independent attestation that the launch matched the declaration.

The combiner preserves legacy whole-second artifacts, but derives a wall-time
range from their UTC buckets and per-replica monotonic durations. In that case,
`metrics.throughput_tok_s` is the conservative lower bound; publish it with
`throughput_tok_s_upper_bound`, the timing precision, and the legacy identity
or output-validation limitations. Do not relabel that range as exact
synchronization. Pass inputs in a stable replica order and run the combiner
twice, requiring byte-identical output. Retain both inputs and the resulting
`anvil-serving.capacity-aggregate/v1` artifact under `raw-run-evidence`; the
aggregate preserves input paths and hashes and never replaces its sources.
For a 100-request DP2 population, for example, use two synchronized 50-request
replicas rather than describing two unrelated runs as one C16 result.

### Stage gates and command discipline

Use `research`, `feasibility`, `scout`, `finalist`, `quality`, `restoration`,
and `publication` stages. Scout cells can be small and diagnostic. Advance only
correct, complete, identity-pinned, physically feasible survivors to finalist
cells. Finalists use the controlled-output workload and the declared comparison
population. Do not turn a performance winner into qualification until its
quality and integration gates pass or are recorded as missing.

In `capacity-v3`, TPOT and mean ITL are the same per-request aggregate proxy,
not a distribution of raw token-arrival intervals. At exactly 100 samples,
nearest-rank p99 is still one tail order statistic; publish the population and
method, and do not call it a stable service-level tail without more evidence.

Run one concise command per verification step and stop on its nonzero exit.
Do not continue a compound shell block into hashing, finalization, or a broad
worktree inventory after a native command fails. Keep reviews path-scoped and
record the earliest actionable error in `friction-log.md` with a durable
fix-forward artifact and independent verification. A successful retry alone
does not close a friction entry.

For delegated work, send the compact dispatch packet, owned paths, source
artifact identities, authority boundary, stop conditions, and return contract.
Use bounded or no-history delegation when supported instead of replaying the
entire campaign transcript.

## 3. Reuse retained evidence for format-only work

When the request is to migrate or improve an existing finding, do not load a
model, restart a serve, or rerun a benchmark merely to populate the new format.
Use the retained manifest and machine-readable artifacts when they contain the
exact identity, configuration, measurement protocol, sample counts,
distributions, failures, decision, and raw evidence links required by the
publication contract.

Derive headline values from those artifacts and reconcile the artifact
manifest, decision summary, result card, publication summary, and detailed
finding in tests. Do not reconstruct a missing metric from prose, round
incompatible measurements into agreement, or turn an absent field into a
claim. Record a publication gap when the retained evidence is insufficient; a
format migration is not authority for a live rerun.

## 4. Apply the publication matrix

For generated recipe-result catalogs, use the tested product workflow:

```text
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --confirm
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --check
```

The confirmed form writes; `--check` verifies without writes. Both resolve
identity, workload fields, numeric metric paths, and source hashes from the
reviewed catalog and native artifacts. Do not substitute an untracked one-off
generator.

For every meaningful result:

- add or update the immutable dated finding and raw artifact links;
- update `docs/findings/README.md`;
- add the run to `docs/benchmarks/runs.md`;
- create or update its dossier under `docs/benchmarks/models/`; and
- update the measured hardware page.

Update `docs/BENCHMARKS.md` only when the current recommendation, reference
deployment, or reader-facing comparison changes. Update
`docs/benchmarks/methodology.md` only when the workload or evidence contract
changes. Update the portal when a current/rollback/challenger decision changes.

If a file mentions the RTX PRO 6000 without measuring it, classify it in
`docs/benchmarks/rtx-pro-6000-audit.md` as `protected/co-resident`,
`topology-only`, or `unrelated`. Never list an RTX 5090 run as a PRO 6000
benchmark.

Complete the publication as one transaction. Before stopping, verify that the
new finding is reachable from the findings index, run catalog, dossier, and
hardware page; verify that the run-catalog row links both its dossier and
finding. Do not leave a finding-only update for a later pass.

## 5. Keep dossiers consistent and readable

Create or migrate model dossiers with `templates/dossier.md`. Keep the eight
canonical `##` headings exactly as written in that template so every dossier
has the same navigation contract. The template is a presentation scaffold,
not permission to manufacture missing evidence.

At the top, include a `Decision snapshot` with product role, selected or
best-qualified configuration, measured hardware, evidence labels and bounded
headline result, decision/promotion boundary, important limitation, evidence
cutoff, and dossier-review date. Use
**Not recorded in retained evidence** when an item is unavailable. Do not omit
the detailed narrative: split multi-review histories into dated or
outcome-oriented subsections and preserve the technical reasoning below the
snapshot.

Use one subsection per materially different configuration or measurement
lane. In evidence sections, label `Status`, `Measured`, `Limits`, and
`Evidence`, then retain the complete narrative needed to interpret them.
Group decisions into retained/selected and rejected/superseded/incomplete
states. Group long failure lists by cause and give each item a bold lead label.
Link the exact tracked recipe or container reconstruction page near the
configuration summary. If the managed recipe is operator-private or contains
private topology, link a sanitized public reconstruction page such as
`docs/benchmarks/configurations.md` instead; never publish a private recipe
path, endpoint, host identity, or secret-bearing command.

For format-only migrations, reuse retained evidence under section 2. Never
rerun a model merely to make dossiers symmetrical, and never turn an unknown
identity, metric, comparison, or gate into a placeholder claim. Omit empty
optional subsections rather than publishing invented content.

## 6. Make measured findings publication-ready

For a local `functional`, `capacity`, or `quality` result, add both:

- a `benchmark-result-card/v1` near the top of the finding using
  `templates/finding.md`; and
- a companion `benchmark-publication-summary/v1` publication summary using
  `templates/publication-summary.md`.

Follow `docs/benchmarks/finding-format.md` and the exact rules in the
publication contract. Put the local hardware/configuration, context,
concurrency, managed recipe, measurement path (online/routed, offline, or
kernel-level), warm/cold state, important negative result, and evidence link
close to every headline number. Include copy-ready short-post and Reddit
variants, accessible screenshot alt text, and a claim ledger that maps each
public claim to the finding or raw artifact.

Link both the common `artifact-manifest.json` and the human `README.md` evidence
index from the result card. The manifest inventories native evidence; it is not
a replacement for raw artifacts.

When a campaign has two or more comparable performance cells, create a
machine-derived chart pack. Put a graph manifest beside the native artifacts
and run:

```text
python skills/anvil-serving-benchmark-docs/scripts/plot_benchmark_matrix.py PATH/graph-manifest.json
```

The manifest must name exact retained JSON artifacts and numeric metric paths;
never copy headline numbers into the graph specification. Retain the generated
SVG and graph-data JSON, link both from the evidence index and finding, and run
the renderer twice to verify deterministic output. Prefer a small matrix that
shows the decision: context, concurrency, TTFT, effective prefill, decode,
TPOT/ITL, E2E, and aggregate throughput as applicable. Do not mix unmatched
hardware, checkpoint, runtime, quantization, or warm/cold conditions in one
series without labeling the break.

Use the native artifact's timing methodology. For `capacity-v3`, TPOT and mean
ITL are the same per-request aggregate—generation time divided by completion
tokens after the first token—and are not a raw timestamp for every token.
Report p50 and p95 when retained, keep TTFT distinct from first reasoning
output, and label effective prefill as including queueing, scheduling, prefill,
and first-token work.

After the evidence and restoration are final, use the installed
`engineering-learning` skill for any reusable lesson exposed by the campaign.
Capture one evidence-linked lesson per entry—for example a runtime-version
fence, effective-concurrency rule, or restoration invariant—not a duplicate of
the benchmark summary. Keep raw benchmark artifacts and decisions in this
repository; the learning entry points back to them.

Close every friction-log entry with a fix-forward disposition. Prefer an
in-scope code, recipe, test, or skill fix when the root cause belongs to Anvil
Serving. Otherwise create or update a tracked product-gap ticket, retain an
upstream issue/watch item, or codify an operational invariant and engineering
learning. Record the durable artifact beside the friction entry. A successful
manual retry is recovery evidence, not process improvement, and must not be the
only disposition. Re-run the smallest independent gate that proves each
fix-forward change before closing the campaign.

Treat the card and publication summary as derivative views. They never replace the dated
finding or raw artifacts, and a platform-ready sentence must not broaden the
measurement universe into a universal `best`, `fastest`, or `strongest` claim.
Research-only, readiness-only, and failed-load findings may use a result card;
their publication summary is optional unless a compact public communication is
required.

## 7. Preserve decision boundaries

Publishing documentation never authorizes `serves up`, `serves promote`,
router configuration, alias changes, teardown, or rollback. Record
`no-promotion` explicitly when a run qualifies a challenger but no human
promotion occurred. Preserve negative and incomplete runs.

Do not rewrite an older finding to match a newer conclusion. Add a new finding
or linked erratum; keep stable URLs and compatibility pages.

## 8. Validate

Run the installed `skill-creator` skill's `quick_validate.py` against
`skills/anvil-serving-benchmark-docs`. Parse every JSON file in
`templates/artifact-set/`, then run:

```text
python -m pytest tests/test_benchmark_docs_skill.py -q
python scripts/check_markdown_links.py --root .
python -m mkdocs build --strict
python scripts/audit_cli_references.py --update --scope full
python scripts/audit_cli_references.py --check --scope full
```

Then run the repository's Ruff and full pytest gates. Report incomplete live
evidence separately from documentation/test success.
