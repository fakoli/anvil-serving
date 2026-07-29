---
name: anvil-serving-benchmark-docs
description: Publish and maintain hardware-first benchmark documentation in anvil-serving. Use whenever a model is newly configured, served, benchmarked, qualified, promoted, rolled back, or fails meaningfully, including LLM, vision, Omni, STT, and TTS results. Separates measured hardware from protected/co-resident topology and local evidence from research priors; never authorizes a serve or route change.
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

## 2. Apply the publication matrix

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

## 3. Preserve decision boundaries

Publishing documentation never authorizes `serves up`, `serves promote`,
router configuration, alias changes, teardown, or rollback. Record
`no-promotion` explicitly when a run qualifies a challenger but no human
promotion occurred. Preserve negative and incomplete runs.

Do not rewrite an older finding to match a newer conclusion. Add a new finding
or linked erratum; keep stable URLs and compatibility pages.

## 4. Validate

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
