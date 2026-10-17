# 2026-10-16 GLM natural-output baseline evidence

This is a closed public-safe baseline bundle for the current GLM-5.3-Flash r11
route. The timing matrix is retained and coding repeatability was not
established. It does not support a qualification, replacement ranking, or
promotion decision.

## Closed evidence index

The [common manifest](artifact-manifest.json) binds every retained file to its
size, digest and role. Its [source inventory](artifact-manifest-source.json)
contains all ten roles and no pending artifacts.

- Plan and identity: [run plan](run-plan.json), [workload manifest](workload-manifest.json),
  [configuration pins](configuration-identity.json), [source registry](source-registry.json),
  and [coding amendment](coding-amendment.md).
- Timing: [summary](timing-summary.json), [summary provenance](timing-summary-provenance.json),
  [source hashes](timing-source-hashes.json), [graph manifest](graph-manifest.json),
  [SVG chart](graphs/timing-matrix.svg), and [graph data](graphs/timing-graph-data.json).
- Coding R1: [native SWE run](native/cached-swe-r1.json),
  [official grader report](native/cached-swe-r1-official-grader.json), and
  [aggregate campaign extraction](native/cached-swe-r1-campaign-evidence.json).
  R1 resolved 4/5 with complete independent grading.
- Coding R2: [native SWE run](native/cached-swe-r2.json),
  [official grader report](native/cached-swe-r2-official-grader.json), and
  [failed campaign extraction](native/cached-swe-r2-campaign-evidence.json).
  R2 resolved 0/2 graded patches; three empty step-limit attempts were ungraded.
  The failed aggregate extraction has no score.
- Interpretation: [final coding summary](coding-summary-final.json),
  [independent R2 review](independent-r2-review.json), and [decision summary](summary.json).
- Failures, continuity and limits: [friction log](friction-log.md), [errata](errata.md),
  and [serving continuity with open storage limitation](restoration.json).
- Provenance: [redaction ledger](redaction-ledger.json) and
  [SWE source/public bindings](native/swe-redaction-bindings.json).

The twelve native timing artifacts retain their original metrics and request
fields. Only the routed endpoint is replaced with the non-routable
`redacted://routed-capability` value.

| nominal context | concurrency | repeat 1 | repeat 2 | repeat 3 |
| ---: | ---: | --- | --- | --- |
| 8,192 | 1 | [r1](native/natural-ctx8192-c1-r1.json) | [r2, retained failure](native/natural-ctx8192-c1-r2.json) | [r3](native/natural-ctx8192-c1-r3.json) |
| 8,192 | 4 | [r1](native/natural-ctx8192-c4-r1.json) | [r2](native/natural-ctx8192-c4-r2.json) | [r3](native/natural-ctx8192-c4-r3.json) |
| 32,768 | 1 | [r1](native/natural-ctx32768-c1-r1.json) | [r2](native/natural-ctx32768-c1-r2.json) | [r3](native/natural-ctx32768-c1-r3.json) |
| 32,768 | 4 | [r1](native/natural-ctx32768-c4-r1.json) | [r2](native/natural-ctx32768-c4-r2.json) | [r3](native/natural-ctx32768-c4-r3.json) |

Native SWE stages and official grader reports are authoritative per task. The
aggregate extractions preserve the existing wrapper evidence and do not add
per-task comparison semantics. Private wrappers, trajectories and raw process
output remain private. Task-image tags and immutable digests are retained at
each image observation point.

## Boundaries

Natural output has variable length. Completion-token rates can include
reasoning tokens and are not controlled-decode or visible-only speed. The
router was shared and the workload snapshot was unavailable, so these are not
traffic-isolated or engine-only measurements. Each timing cell has 20 requests;
three repeats may describe repeat-median ranges but cannot establish stable
p99 latency or service stability. The retained matrix has one no-visible-output
failure and two long C1/32K reasoning outliers; neither is filtered.

The original plan stated that finish reasons would be retained. Successful
native timing rows retain visible-output and terminal-observation timing, but
not finish reasons, terminal form, raw usage objects, or visible character
counts. [`errata.md`](errata.md) records this correction.

The coding baseline now uses the existing pinned five-task set twice because
the fresh ten-task images are uncached and storage reserve is constrained. It
is a repeatability observation, not a fresh held-out SWE score; see
[`coding-amendment.md`](coding-amendment.md).

All planned attempts are exhausted; grading is incomplete for R2 and coding
repeatability was not established. Full SWE-interval server logs and per-retry
transport telemetry are unavailable. Serving continuity was verified, but the
post-run storage check was below the 100 GiB admission reserve. The cause is
unproven, no pruning was performed, and further trials require that separate
storage gate to be resolved.
