# Frozen coding fixture and independent grader

Seven pure-Python tasks were collected three times per model. The frozen
`suite.json` SHA-256 is
`3f7f061135313c81e9002f9971f682ee80d03ae485f566cfdb2fd17ea9b7c57a`.
The original suite and grader are byte-identical to the reviewed private inputs.
Text markers establish collection only; executable held-out cases establish the
reported functional scores.

From this evidence directory, reproduce a frozen list-pair grade with:

```text
python fixture/grade.py native/quasar-text32k-coding-raw.json --reviewed --expected-repetitions 3
python fixture/grade.py native/swift15-text32k-coding-raw.json --reviewed --expected-repetitions 3
python fixture/grade.py --self-check
```

A behavioral failure returns exit status 1 and still emits its complete grade.
Only the original metadata fields named in `redaction-lineage.json` were
redacted; prompts, visible code, reasoning and case values were preserved.
Grade `artifact_sha256` values identify the pre-redaction native bytes; the
lineage ledger maps these to the public copies.

The separately named `*-coding-grade-tuple-edges.json` files are post hoc input
representation sensitivity analyses. They reconstruct only the
`stable_toposort` edge pairs as tuples immediately before candidate invocation,
using the same five cases and expected outputs. They do not replace the frozen
list-pair grades or change the collected model responses. The prompt describes
`(before, after)` pairs without explicitly requiring lists.

All retained sources were manually reviewed before offline execution; see
`source-review-notes.md`. `--reviewed`, the AST allowlist and subprocess timeout
are safeguards for this bounded fixture, not a hostile-code security sandbox.
This fixture does not measure repository-level SWE performance.

These synthetic prompts and cases were authored for this campaign and follow the
repository license. No external benchmark corpus was incorporated.
