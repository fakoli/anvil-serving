# 2026-10-16 GLM natural-output baseline evidence

This is a closed public-safe baseline bundle for the current GLM-5.3-Flash r11
route. The timing matrix is retained and coding repeatability was not
established. It does not support a qualification, replacement ranking, or
promotion decision.

## Retained so far

- Twelve native `anvil-serving.benchmark/v1` timing artifacts under
  [`native/`](native/). Their routed endpoint field is replaced with the
  non-routable `redacted://routed-capability` value; all other native metrics
  and request timing fields are retained.
- [`run-plan.json`](run-plan.json), [`workload-manifest.json`](workload-manifest.json),
  and [`configuration-identity.json`](configuration-identity.json) are
  public-safe derivatives of the private campaign plan and identity snapshots.
- [`timing-summary.json`](timing-summary.json) derives the 239/240 timing
  result without flattening its native sources. The deterministic
  [`graph-manifest.json`](graph-manifest.json) produces the retained SVG and
  graph-data pack under [`graphs/`](graphs/).
- [`redaction-ledger.json`](redaction-ledger.json) binds each copied
  native artifact to its private-source SHA-256 and the only transformation.
- [`native/cached-swe-r1.json`](native/cached-swe-r1.json) retains the first
  cached five-task SWE stage: 4/5 resolved and 5/5 independently graded. Its
  companion [`native/swe-redaction-bindings.json`](native/swe-redaction-bindings.json)
  binds the public derivative to the private native stage. The private wrapper,
  trajectories, and raw process output are not published; R2 is retained as a terminal incomplete result.
  The stage retains its public SWE-bench task image tags and immutable digests
  at each observation point, plus the paired task-image mapping.
- [`native/cached-swe-r1-campaign-evidence.json`](native/cached-swe-r1-campaign-evidence.json)
  is a derived, public-safe extraction of the terminal wrapper's existing
  `benchmark-evidence/v1` payload for campaign-level comparison. It retains the
  workload gates and aggregate 4/5 score only; it has no per-task comparator
  semantics. The native SWE stage and official grader report remain the
  authoritative per-task evidence.

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
