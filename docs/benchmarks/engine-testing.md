# Engine testing

Engine investigations test a pinned runtime, configuration, and workload. They
do not select a route or promote a model. The reference incumbent in these campaigns was the
separately qualified NVIDIA FP4 GLM configuration; every Flare result below is
`no-promotion` and the tested Ignis release is **0.7.2**, whose fixed coding
run was incomplete.

## Current evidence

- [2026-10-23 upstream refresh](../findings/2026-10-23-flare-upstream-refresh.md):
  a compatibility-only baseline after a minimal template fix. It retained
  reverse replays and restoration, but did not run the allocation comparison or
  numerical qualification.
- [2026-10-23 state diagnostics](../findings/2026-10-23-flare-state-diagnostics.md):
  bounded state checks passed; protected latency and the full numerical gate
  remain held.
- [2026-10-22 cache allocation diagnostic](../findings/2026-10-22-flare-cache-fix.md):
  a fixed-work replay supports one diagnostic mechanism only; other gates keep
  the incumbent.
- [2026-10-19 Ignis 0.7.2 qualification](../findings/2026-10-19-ignis-072.md):
  bounded serving checks passed, while the five-task coding run produced one
  unresolved patch and four empty ungraded submissions.

## Report series

Read the reports in order because each one narrows a different question:

1. [Initial Flare scout](../findings/2026-10-21-flare-glm53-trial.md) and
   [optimization follow-up](../findings/2026-10-21-flare-glm53-optimization.md)
   establish the bounded candidate and its configuration tradeoffs.
2. [Inference diagnostics](../findings/2026-10-21-flare-inference-diagnostics.md)
   and [cache diagnostics](../findings/2026-10-22-flare-cache-diagnostics.md)
   retain the counter and replay observations behind the allocation hypothesis.
3. [Cache allocation](../findings/2026-10-22-flare-cache-fix.md),
   [state/protected-regression diagnostics](../findings/2026-10-23-flare-state-diagnostics.md),
   and [upstream refresh](../findings/2026-10-23-flare-upstream-refresh.md)
   retain the later controls, failures, and restoration evidence.

Each finding links its closed artifact manifest and human evidence index. Use
the [run catalog](runs.md) for the chronological record and the
[GLM dossier](models/glm53-flash.md) for model-level interpretation.

Publication copies received an additional privacy pass on October 24. Affected
bundles include `publication-transformations.json`, recording the previous and
replacement hashes for generic machine labels, runtime hostnames and import
formatting. Their manifests and public provenance describe the new copies;
the original experiment evidence and numerical results remain unchanged.

## Repeat an investigation

Use the [methodology](methodology.md) to define comparable measurements and
the [repeatable-campaign guide](repeatable-campaigns.md) to freeze the plan,
retain native evidence, and restore the incumbent. The
[finding format](finding-format.md) explains the public result card and
artifact set. A repeat must be a new dated campaign with its own evidence; it
does not extend or promote these results.

1. Pin the engine, image, model, configuration, hardware and workload; capture
   the incumbent and a managed restoration plan before taking it down.
2. Verify model identity and metrics after every swap. Retain the scrape window,
   server logs and request timestamps through the last coding task, including
   failed requests and cancellation recovery.
3. Run independent functional and quality gates before ranking performance.
   Record configured context separately from actual prompt tokens and measured
   concurrency. Empty submissions remain in the attempted population.
4. Change one setting at a time. Compare matched work, include small and
   page-heavy cache controls, and measure instrumentation overhead separately.
5. Restore and verify the incumbent, close the coverage ledger, then publish
   the result, raw artifacts, source pins, failures and missing cells together.

## Bounded coding diagnostics

Use the versioned [16K Aider profile](https://github.com/fakoli/anvil-serving/blob/3e211eec5998998712acba85cf2d06ae4a49cd1a/configs/benchmarks/aider-diagnostic-16k-v1.json)
or [32K Aider profile](https://github.com/fakoli/anvil-serving/blob/3e211eec5998998712acba85cf2d06ae4a49cd1a/configs/benchmarks/aider-diagnostic-32k-v1.json)
for a single-task diagnostic. They retain identical pinned Aider/Polyglot
revisions, two edit attempts, at most twelve model calls, zero retries, a 600-second
suite deadline and a 20 GiB free-disk requirement; only the completion cap
differs. `pilot-v2` remains unchanged. Freeze the task, profile digest,
sampling and reasoning controls in the existing campaign specification.

Verify requested controls separately from the body forwarded by the router
and the behavior supported by the engine. A tier's hard reasoning override
can prevent a lower requested setting from reaching the engine even when the
request is accepted. Keep unsupported control cells blocked and visible; do
not substitute the configured setting and call it a comparison. Offline wire
capture proves forwarding only.

The [reasoning-limit probe packet](reasoning-limit-probe.md) prepares a small
numeric-budget enforcement check. It remains unexecuted and requires a
notified, isolated managed request path before any running-model action.

New Aider runs declare versioned request accounting: every started transport
attempt must have exactly one response or sanitized error before accounting
can be complete. Final summarization joins within the suite deadline; an
unsettled request leaves incomplete evidence. Transport errors retain unknown
token usage. Historical artifacts keep their original contract and grades;
the stricter contract does not reconstruct missing historical responses.

Report native grades, job completion, diagnostic coverage and measurement
eligibility separately, following the [Aider evidence contract](methodology.md#aider-grades-lifecycle-and-diagnostic-observations).
Use the [whole-batch admission rule](repeatable-campaigns.md#reserve-the-complete-batch-window)
before execution. These profile and harness contracts establish no live model
result or promotion decision.

## Instrumentation and source availability

The Flare investigations used local Anvil source additions for authenticated
scheduler capture, monitoring registration and capacity configuration. Their
retained measurements do not establish that those additions are released or
installed on another host. The broader monitoring and secret-mount lifecycle
changes remain a separate review unit; use the published command help and
managed recipe surfaces as the authority for an installed version.

The existing qualification, kernel-tuning and benchmark-publication skills
provide the repeatable workflow. A new plugin is unnecessary. Keep private
runtime configuration and unredacted traces outside the public artifact bundle.

## Ignis author-request check — 2026-10-24

The [author's October 19 request](https://github.com/fakoli/anvil-serving/issues/660#issuecomment-6262384434)
specified 0.7.2, the RTX PRO 6000 profile, eight lanes/MTP3, a pinned Flash Next
model revision, 52 GiB containment and 131,072 configured context. The linked
October 19 trial executed that configuration. Its 100,739/103,256-token probes
do not qualify the full configured window, and its coding result remains
attempted 5, graded 1, resolved 0, with four empty ungraded submissions.

[Ignis 0.7.3](https://github.com/gpillon/ignis/releases/tag/v0.7.3), published
October 22, is a distinct, untested candidate. No additional GPU run was made
for this publication. The next useful 0.7.2 experiment is the same frozen
five-case coding workload with complete request/server correlation and official
grading coverage. Empty retained submissions cannot be repaired by offline
regrading. A 0.7.3 trial needs its own image digest and full qualification.
