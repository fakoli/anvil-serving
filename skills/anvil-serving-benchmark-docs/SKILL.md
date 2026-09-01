---
name: anvil-serving-benchmark-docs
description: Publish and maintain hardware-first, publication-ready benchmark documentation in anvil-serving. Use whenever a model is newly configured, served, benchmarked, qualified, promoted, rolled back, or fails meaningfully, including LLM, vision, Omni, STT, and TTS results. Separates measured hardware from protected/co-resident topology and local evidence from research priors; never authorizes a serve or route change.
---

# Anvil Serving Benchmark Docs

Use this skill for the publication phase of every meaningful model run. Read
`references/publication-contract.md`, `README.md`, `CLAUDE.md`,
`docs/benchmarks/index.md`, and the applicable model or modality skill before
editing.

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

## 2. Reuse retained evidence for format-only work

When the request is to migrate or improve an existing finding, do not load a
model, restart a serve, or rerun a benchmark merely to populate the new format.
Use the retained manifest and machine-readable artifacts when they contain the
exact identity, configuration, measurement protocol, sample counts,
distributions, failures, decision, and raw evidence links required by the
publication contract.

Derive headline values from those artifacts and reconcile the result card,
publication summary, and detailed finding in tests. Do not reconstruct a missing metric
from prose, round incompatible measurements into agreement, or turn an absent
field into a claim. Record a publication gap when the retained evidence is
insufficient; a format migration is not authority for a live rerun.

## 3. Apply the publication matrix

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

## 4. Keep dossiers consistent and readable

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

## 5. Make measured findings publication-ready

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

Treat the card and publication summary as derivative views. They never replace the dated
finding or raw artifacts, and a platform-ready sentence must not broaden the
measurement universe into a universal `best`, `fastest`, or `strongest` claim.
Research-only, readiness-only, and failed-load findings may use a result card;
their publication summary is optional unless a compact public communication is
required.

## 6. Preserve decision boundaries

Publishing documentation never authorizes `serves up`, `serves promote`,
router configuration, alias changes, teardown, or rollback. Record
`no-promotion` explicitly when a run qualifies a challenger but no human
promotion occurred. Preserve negative and incomplete runs.

Do not rewrite an older finding to match a newer conclusion. Add a new finding
or linked erratum; keep stable URLs and compatibility pages.

## 7. Validate

Run the installed `skill-creator` skill's `quick_validate.py` against
`skills/anvil-serving-benchmark-docs`, then run:

```text
python -m pytest tests/test_benchmark_docs_skill.py -q
python scripts/check_markdown_links.py --root .
python -m mkdocs build --strict
python scripts/audit_cli_references.py --update --scope full
python scripts/audit_cli_references.py --check --scope full
```

Then run the repository's Ruff and full pytest gates. Report incomplete live
evidence separately from documentation/test success.
