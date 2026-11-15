# Publication summary: QUASAR versus Swift15

<!-- benchmark-publication-summary/v1 -->

Bounded by the [dated finding](../2026-10-16-quasar-swift15-coding-tools.md),
[artifact manifest](artifact-manifest.json), and [evidence index](README.md).

## Canonical facts

One RTX 5090, matched pinned NInfer text-only 32K profiles, configured C4 and
request C1. Swift15 passed 18/18 agentic scenarios; QUASAR passed 16/18.
Both passed 20/21 whole coding solutions under the frozen list-pair probe.
QUASAR reached 21/21 under a plausible tuple-pair sensitivity analysis; Swift15's
varint defect remained. Keep Swift15 as default for now; no promotion occurred.
This tiny fixture does not establish a general quality ranking or speed winner.

## Copy-ready short post

On one RTX 5090 with matched NInfer text-only 32K settings, Swift15 passed 18/18
agentic scenarios versus QUASAR's 16/18. Coding was mixed and depended on an
ambiguous pair representation. QUASAR is not a clear replacement; no promotion.

## Copy-ready Reddit variant

We tested pinned QUASAR and Swift15 artifacts on one RTX 5090, with configured
C4 but requests C1. A seven-task coding fixture ran three times per model and
was independently graded. Frozen grades were 20/21 each; QUASAR reached 21/21
when ambiguous edge pairs were reconstructed as tuples. Swift15 had a real varint
bug. QUASAR called the right tools but omitted the requested final status in two
scenarios: 16/18 versus Swift15's 18/18. Timing was not controlled enough to rank
speed. We restored the original Swift15 profile and made no promotion.

## Screenshot alt text

Table of matched RTX 5090 functional results: tools 16/18 QUASAR and 18/18
Swift15; frozen coding 20/21 each; tuple-pair sensitivity 21/21 QUASAR and
20/21 Swift15. The table does not establish a general ranking or speed winner.

## Claim ledger

| Claim | Conditions | Native evidence |
| --- | --- | --- |
| Tools 16/18 versus 18/18 | Matched text32K, configured C4/request C1 | [QUASAR](native/quasar-text32k-agentic-raw.json), [Swift15](native/swift15-text32k-agentic-raw.json) |
| Frozen coding 20/21 each | Same seven tasks, three repetitions, 123 cases | [QUASAR grade](native/quasar-text32k-coding-grade.json), [Swift15 grade](native/swift15-text32k-coding-grade.json), [fixture](fixture/suite.json) |
| Tuple sensitivity 21/21 versus 20/21 | Post hoc representation change only | [QUASAR](native/quasar-text32k-coding-grade-tuple-edges.json), [Swift15](native/swift15-text32k-coding-grade-tuple-edges.json), [review](source-review-notes.md) |
| Inputs and reasoning preserved after redaction | Only declared operator metadata changed | [lineage](redaction-lineage.json), [raw index](raw-evidence-index.md) |
| Original profile restored; no promotion | Host identity sources remain private | [restored preflight](native/final-restored-preflight.json), [verification](native/final-restored-verification.json), [decision](summary.json) |
