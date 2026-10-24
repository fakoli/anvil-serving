# Flare upstream refresh: result and contribution recommendation

Observed October 23, 2026 Pacific / October 24 UTC. The run is closed and the exact incumbent is restored. No default changed, no model was promoted, and nothing was pushed or submitted upstream.

## Outcome

We found a small, concrete compatibility fix that is ready for the user's review, and completed the refreshed baseline needed to evaluate the larger cache proposal. The current baseline still performs substantial replay prefill; the next useful allocation test is an otherwise matched 25%/75% comparison. This run did not perform that comparison and cannot claim a current-main allocation speedup.

The authoritative numerical view is [decision-summary.json](decision-summary.json), derived from [the complete native analysis](archives/accepted-summary.tar.gz). The latter binds raw response/proof hashes and explicitly preserves missing work dimensions. [Raw closure](archives/canonical-root.tar.gz) records a successful restoration with no experiment or recovery errors. Final independent review is retained beside the evidence.

## What changed upstream

We screened all 37 PRs and 34 issues and read the relevant cache, correctness, latency and frontend changes in detail. This is not a claim to have deeply reviewed every unrelated diff. A final [GitHub recheck](archives/source.tar.gz) found the same main commit and unchanged inventory.

Merged #38 already supplies much of the retention/decision telemetry we had explored locally. We used it instead of transplanting our diagnostic patch. The quarter-budget row allocator remains. Open #16 changes GLM shifted draft-state capture and needs a new export. Merged #35 adds selected-ID scores, also requiring a compatible export. #43 and #69 address different concurrent/prefill conditions from our zero-prefill warm controls. #57's proposed aggregate metrics do not supply request-to-slot correlation. [The source-backed overlap review](upstream-review.md) records details, source links and dispositions.

## Compatibility fix

The current annotated GLM template becomes byte-identical to the retained 10,950-byte legacy asset when its six marks are removed. Current main's shared exact-byte upgrader recognizes other deployed families but omits GLM. Local commit `0b21d7f4883ac6f2b099df55813e8be06777b9df` adds GLM to that list and adds rendering/token/boundary regressions plus the corresponding documentation entry: three files, no allocator or kernel change.

Four focused CPU tests and the release build passed. These include the independent MiniJinja reference, Unicode, reversed tool-result arrival order, exact token/boundary matching, stable prefix cuts, and rejection of altered legacy templates. Signed package/weight verification then passed on six fresh GPU startups, followed by the smoke and diagnostic workload. The first CPU command failed because it omitted a required feature; that command failure is retained, and correcting the feature selection required no source change.

[The reviewable patch](legacy-glm-template-compat.diff) and [detailed PR draft](compatibility-pr-draft.md) explain the failure trigger, shared fix and validation limits. The repository allows a focused PR without opening an issue first. Design review passed before the local commit. A final user review remains before upstream submission.

## Pinned experiment

| Item | Value |
| --- | --- |
| Upstream / local change | `d5fd9824593c2a70f6bdc001b39f16b4c5b3b0b4` / `0b21d7f4883ac6f2b099df55813e8be06777b9df` |
| Candidate model | `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD` |
| Retained checkpoint | `dec48abd33efa73c3bb7c95b74eee10cad34f9be` |
| Runtime image | `sha256:b3c33bcf858d3764dbe155cbda5a006d364fa3a4309922bcb92e51e925c05804` |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q 96-GB GPUs; TP2 |
| Scheduling / draft | Configured C4, serial diagnostic requests; draft count 2, adaptive disabled |
| Cache | Balanced profile; 32-GiB host cache; disk disabled; default 25% row allocation |
| Startup capacities | 52 continuation rows; 7,314 page slots |
| Context distinction | Workload ceiling configured as 32,768; native context 1,048,576; aggregate KV 1,858,560 tokens. None proves simultaneous full-window capacity. |
| Driver | 615.71.10 after system-update recovery |
| Package policy | Signed assets and captured graphs retained; no re-export or kernel change |

Each of the three pressure repetitions used a fresh server, three warmups, 52 forward priming requests and 52 reverse replay requests. Separate fresh servers handled the four schema fixtures, the small-working-set control and the page-heavy control. All bodies were pinned, one output token was requested, and prompt/output IDs plus native metrics were retained. Package sampling clamps temperature zero into its supported range; this is not a greedy-decoding claim.

## Measured results

| Population | Measured requests | Median first-generated-token TTFT | p95 TTFT | Host fetch completions | Prefill tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reverse replay 1 | 52 | 449.93 ms | 895.97 ms | 10 | 199,464 |
| Reverse replay 2 | 52 | 452.90 ms | 899.29 ms | 10 | 199,464 |
| Reverse replay 3 | 52 | 460.25 ms | 901.37 ms | 10 | 199,464 |
| Small warm set | 24 | 9.382 ms | 18.849 ms | 0 | 0 |
| Page-heavy warm set | 12 | 18.065 ms | 39.830 ms | 0 | 0 |

The pressure repetitions each recorded 52 host lookups, 10 admissions/fetch completions, 42 native host `no_match` outcomes and 76,848 host-reused tokens. A host `no_match` can accompany a GPU hit; it is not a count of 42 full-prompt recomputations. The warm controls are separate populations and are not pooled with the pressure workload. Their zero host fetch/prefill is consistent with the intended warm GPU-cache condition.

All **391 adapter requests plus one smoke** completed without request failures across **six fresh servers**: 4 fixtures, 15 warmups, 180 priming and 192 measured requests. All 192 measured one-token outputs matched their corresponding priming token IDs, prompt IDs and exact request-body hashes. This is a narrow, useful correctness check. Per-request speculative/accepted/emitted work is not available in the current response schema and remains unknown. No throughput, long-output quality or production tail-latency guarantee follows from these small serial populations.

## What the baseline establishes

The current default still has costly replay work under this workload, while warm small/page-heavy requests are much faster. The unchanged row split is a plausible tuning target supported by the earlier controlled experiment, but native counters in this run do not expose direct row/page pressure. A new 75% arm is needed to establish the current allocator effect.

The historical 95.04–96.06% replay TTFT reduction came from source `47581cca`, an older retention implementation and driver 615.71.09. It remains diagnostic historical evidence, not a current-main speedup. Historical 128-token sampled-output equality and protected-regression failures remain unresolved. A state-copy round trip cannot exclude state omitted from both copies; PR #16 is neither established nor ruled out as the cause. No parity comparison to the incumbent ran here.

## Failures and recoveries retained

1. **System update:** NVIDIA userspace moved to 615.71.10 while the loaded module remained 615.71.09. Managed readiness/restore checks correctly stopped. Idle compute modules were reloaded without a reboot; persistence and CDI were reconciled through their infrastructure owner, and the GPU exporter was recreated through its Compose owner. Both GPUs now report 615.71.10. The dated infrastructure recovery note and raw receipts distinguish this from a Flare defect. Local observability checks passed; the separate pre-existing unavailable Secondary Node target prevents a fleet-wide green claim.
2. **Monitoring mapping:** the new engine identity lacked its operator monitoring contract. Adding the explicit mapping fixed registration before another attempt. A product-gap ticket records the need to detect this in preview, before model downtime.
3. **Collector identity:** Flare advertises the served alias in the model catalog while emitting its canonical package model ID in response events, as documented. Our collector initially required the alias everywhere. It was corrected to bind exact requested/catalog/canonical identities; the failed HTTP fixture remains a failed attempt.
4. **Offline report checks:** independent review caught incomplete closure/population validation and mishandling of labeled Prometheus counters. The final reader preserves labeled monotonic counters separately from changing gauges. Its first closed-data analysis also exposed a fixture-proof binding mistake: the fixture binds its own later observation of the same server, not the initial observation's bytes. The rejected analysis and pre-fix reader are retained; no raw run data were edited or reclassified.

The three failed predecessor windows, their cleanup, the driver recovery, and the final successful window remain separate. Their counts are not silently pooled into the final baseline. Runtime checks, CPU checks and documentation checks are distinct evidence.

## Restoration and next contribution

The exact retained incumbent was restored, monitoring synchronized, and both direct and routed preflight passed: seven checks and 27 observations on each path, including 20 tool probes and the separate tool-result continuation observations. Final managed inventory verified the candidate absent and the original serving identity active. The existing cloud provider choices and unrelated work remained intact.

The immediate contribution is the focused compatibility PR. The [allocation issue draft](issue-draft.md) now includes the current baseline and acknowledges upstream work. Before an allocation PR, run matched current 25%/75% arms with a patched-default control and repeated protected lanes, keep package/export changes constant, and retain the separate numerical-quality gate. Evaluate #16 with matched exports or an independent component oracle; use #35's score interface rather than recreating it. Request-correlated tracing is a later diagnostic if protected regressions remain unexplained.

The notebook's `FLARE-UPSTREAM-001` entry links this campaign and the prior findings. Configurations, source pins, raw evidence and recovery records are retained for a repeatable trial and later conversion decision. `promoted=false`; upstream submission remains pending user review.
