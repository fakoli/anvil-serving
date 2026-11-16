# TensorFold Go validation evidence

Two focused C1 quality jobs checked the same Go `alphametics` task once per
response cap. The 16K job failed both contained tests after two length-finished
responses. The 32K job passed its first contained test; both captured transport
responses stopped normally. This is one unique task and two profile checks,
with no broad Go score, causal cap-benefit or performance claim.

- [Finding](../2026-11-08-tensorfold-go-validation.md) and
  [decision summary](summary.json).
- [Case observations](case-diagnostics.json) derive from the retained native
  [16K result](go-16384/aider/result.json),
  [32K result](go-32768/aider/result.json), request streams and independent
  contained-test grades.
- [Configuration](configuration.json), [original frozen plan](inputs/plan.json),
  [reviewed amendment](inputs-r2/plan.json) and hash-pinned profiles under
  `inputs/` preserve the exact task, caps, request controls and budgets.
- [Independent review](independent-review.md),
  [post-run custody](restoration.json) and
  [counter consistency](counter-reconciliation.json) retain the final boundary.
- [Setup failure](setup-failure.json) and [friction](friction-log.md) remain
  separate from model grades.
- [Source registry](source-registry.json),
  [projection ledger](projection-ledger.json) and
  [artifact manifest](artifact-manifest.json) bind this public bundle.

The live operational values remain private. Native schemas and selected raw
artifacts are preserved; paths, host/GPU identifiers and the deployed router
port are replaced only where listed in the projection ledger. Byte-exact
request streams, adapter summaries and oracle results are identified explicitly.
The public manifest binds published bytes. Native references inside projections
still commit to original private bytes, including omitted preparation-stage
files; they are not silently rewritten to imply a complete public custody
graph. The unsanitized originals and full source/environment verification remain
with the operator. No source code or upstream model weights are vendored here.

Whole-job budget exhaustion differs from a response-generation limit. Native
Aider usage zeros or `num_exhausted_context_windows` labels do not override the
complete transport stream or establish exhaustion of the 327,680-token serving
context. Derived non-reasoning tokens are not measured visible tokens. Process
text is capture-truncated; complete model prose is not claimed.

Both jobs used ordinary managed submission, with explicit per-job identity,
worker-exit and engine-drain checks. They did not acquire managed-campaign
activation custody. Shared traffic was possible; observed counter agreement
does not establish isolation or throughput. Cache state remained uncontrolled,
with its policy preserved. No live model, route, context, reasoning/output
default, cache-policy, lifecycle or promotion change accompanies this result.

Performance, observer overhead, full-context concurrency and full 17-family
benchmark coverage were outside this check. No social publication copy was
requested, so that manifest role is not applicable.
