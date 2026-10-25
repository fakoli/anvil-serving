# GLM coding diagnostics: output limits and compiler failures

**Date:** 2026-10-25

**Scope:** GLM-5.3-Flash-FP4-Local on two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs, routed online through the existing serving tier; concurrency one.

**Decision:** Diagnostic evidence only; no promotion or engine comparison.

<!-- benchmark-result-card/v1 -->
## Result card

> Six targeted Go/Rust cases completed on the incumbent GLM configuration: one passed and five failed. Increasing the completion ceiling from 16K to 32K did not resolve the observed Go output exhaustion; Rust failures were compiler-confirmed coding errors.

| Setup | Qualified value |
|---|---|
| Model | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`; synthetic served identity `served-model-1` |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs on one Linux inference owner; co-resident harness and contained test execution |
| Runtime | vLLM B12X release 2026-10-15; FP4 weights, FP8 KV, TP2/DCP2, MTP3; exact identity commitments in the evidence bundle |
| Recipe | Managed incumbent recipe, unchanged; see [configuration reconstruction](../benchmarks/configurations.md) and [retained identity controls](2026-10-25-glm-diagnostics-evidence/identity-control-map.json) |
| Measurement path | Routed online Aider/Polyglot task execution; cache condition uncontrolled |
| Contract | Configured serving context 327,680 and C4 capacity; this workload used C1, 16,384 or 32,768 completion tokens, requested max reasoning, temperature 0.6/top-p 0.95, and two edit attempts |
| Evidence | Quality diagnostics: six graded jobs with complete identity/drain/telemetry receipts; two unstarted jobs and four blocked requested controls |
| Decision | No model, route, provider or lifecycle change; no Ignis run |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Targeted cases passed | 1 / 6 | Two previously failing task types; not a representative model score |
| Go cases passed | 0 / 3 | One 16K case and two 32K cases; two attempts each |
| Rust cases passed | 1 / 3 | One 16K case and two 32K cases; the pass required repair |
| Requested coverage | 6 measured, 2 unstarted, 4 blocked | Eight supported jobs were frozen; lower reasoning could not be selected through the current route |

**Why it matters:** The additional instrumentation distinguishes output truncation from incorrect code, while the new admission rule prevents starting a job with an inadequate remaining timeout.

**Important caveat:** The sample is small, deliberately difficult and incomplete. The successful Rust case stayed below 16K tokens on both responses, so it does not establish that the larger cap caused success. There is no performance ranking or model promotion.

[Artifact manifest](2026-10-25-glm-diagnostics-evidence/artifact-manifest.json) · [Evidence index](2026-10-25-glm-diagnostics-evidence/README.md) · [Authoritative summary](2026-10-25-glm-diagnostics-evidence/summary.json)

## Method and exact controls

The two tasks were Go `alphametics` and Rust `accumulate`. The independent test oracles, starter files, reference solutions and two-attempt grading contract were unchanged. Aider was pinned to `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`; Polyglot to `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`. Preparation verified failing starters and passing reference solutions.

The [16K](https://github.com/fakoli/anvil-serving/blob/3e211eec5998998712acba85cf2d06ae4a49cd1a/configs/benchmarks/aider-diagnostic-16k-v1.json) and [32K](https://github.com/fakoli/anvil-serving/blob/3e211eec5998998712acba85cf2d06ae4a49cd1a/configs/benchmarks/aider-diagnostic-32k-v1.json) profiles differ in completion ceiling. Each admits one task, two edit attempts, at most twelve model calls, no retries and a 600-second suite deadline. Max reasoning and sampling requests were held fixed. An offline test through the actual router adapter chain verified forwarding of both caps. Live response metadata independently observed exact 16K/32K exhaustion.

The router's declared control policy prevents caller reasoning overrides. Four requested lower-reasoning cells therefore remain blocked, with no substitute requests. The observed source configuration includes a 49,152-token thinking budget, but this experiment does not prove how the running engine interprets that field. Configured context, output ceiling and reasoning usage are distinct quantities.

## Results

| Task and completion ceiling | Passed / graded | Observed result |
|---|---:|---|
| Go, 16,384 | 0 / 1 | Both responses ended with `length`; each reported 16,384 completion tokens, all as reasoning |
| Go, 32,768 | 0 / 2 | All four responses ended with `length`; each reported 32,768 completion tokens, all as reasoning |
| Rust, 16,384 | 0 / 1 | Both responses stopped normally; repair still failed with `E0594` and a closure-mutability mismatch |
| Rust, 32,768 | 1 / 2 | One generic/`FnMut` repair passed; another omitted a mutable callable binding and failed with `E0596` |

The pinned Aider implementation does not apply edits after a length-finished response. Retained Go evidence shows the starter implementation remained unchanged and its tests failed. This is observed output exhaustion, not evidence of filling the configured input context window.

Rust exercised distinct language requirements. The passing repair supplied generic input/output types, `FnMut`, and a mutable function binding. The failed second 32K repetition supplied `FnMut` but still omitted `mut` on the callable parameter. These are ordinary compiler-enforced requirements of the existing task; weakening the tests would change what is being measured.

The native campaign report distinguishes execution from quality: all six submitted jobs completed with accepted measurements, while five task grades failed. The two unstarted jobs have no grade and are not counted as cancelled or failed model outputs. See the [native report](2026-10-25-glm-diagnostics-evidence/campaign-report.json) and [per-case observations](2026-10-25-glm-diagnostics-evidence/case-diagnostics.json).

## Time, memory and observability

The campaign command ended after 1,562.195 seconds when the next job required 780 seconds but only 575.216 seconds of active allowance remained. The admission calculation reserves 60 seconds for entry, the full 600-second child timeout and 120 seconds for closure. It refused to shorten the job or extend the experiment. The external limit was 45 minutes including cleanup. From the first live command at 21:38:25.332676 UTC through the final postcheck at 22:05:09.328065 UTC, the closure window was 1,603.995389 seconds (26.73 minutes); exact clock and final verification commitments are retained in the summary and restoration evidence.

Engine, GPU, host and serving-cgroup telemetry were complete for all six measured jobs. The post-run snapshot verified the same model/container generation, zero running/waiting requests and no increase in observed host or serving-cgroup OOM counters. Sampled host available RAM ranged from 69.30 to 71.49 GiB across the 297 telemetry rows; the exact byte values are retained in the summary. These sampled extrema are not a continuous minimum or a free-memory guarantee. Existing I/O pressure is a timing confounder. No power, clock, driver or model-memory setting was changed, and this run establishes no power bottleneck.

Twelve Aider response observations expose actual server-reported usage. Three additional request starts, one in each Rust case, lack a captured response; their HTTP outcomes and usage remain unknown. Retained logs and pinned source are consistent with background summarization at shutdown, but request-level attribution is not proven. Completed quality measurements do not imply complete token accounting. Native Aider token totals of zero are preserved as source data, not treated as actual zero consumption.

Buffered responses do not measure true TTFT or token-arrival latency. Small samples, uncontrolled cache state, shared host I/O and incomplete auxiliary-request accounting preclude a speed or efficiency claim.

## Harness changes and historical replay

The source changes add bounded finish-reason and nullable usage observations, contained-command/compiler signals, independent reporting of retained cancelled-task grades, and whole-job time admission before submission/release plus a worker-side check. Missing evidence remains unknown; the task oracles and `pilot-v2` profile are unchanged.

The earlier R12 report still accepts 44 graded jobs: 38 passes and six failures. Its four cancelled jobs remain excluded. The updated reporter separately exposes their retained raw outcomes as pass, fail, unknown and pass. It does not add those passes to the accepted denominator. The [replayed report](2026-10-25-glm-diagnostics-evidence/r12-replayed-report.json) retains that distinction.

The frozen executable source was tested at `3e211eec5998998712acba85cf2d06ae4a49cd1a`: 13,318 tests passed, 111 skipped. Independent review and offline replay preceded the live campaign. Original validation failures, generated-profile metadata repairs and all experiment limitations remain in the [friction log](2026-10-25-glm-diagnostics-evidence/friction-log.md).

## Next experiments, not completed work

1. Verify an engine-supported reasoning budget that leaves room for an answer, then compare it through an explicitly qualified route. Do not infer support from a forwarded field alone.
2. Trace the unobserved auxiliary request lifetime and alias/context metadata so token accounting can cover request termination without changing the native grading contract.
3. Replan the two unstarted repetitions with an adequate declared allowance, then use matched settings for any later Ignis comparison. No such follow-up ran here.

## Evidence boundary

Public files are sanitized derivatives with original-private and derivative hashes recorded separately. Rewritten bytes cannot revalidate the original native identity chain. The retained native originals remain authoritative; public projections preserve grades, numeric observations, failures and coverage rather than raw prompts, reasoning text, credentials or operator topology. No model promotion or upstream engine patch is claimed.
