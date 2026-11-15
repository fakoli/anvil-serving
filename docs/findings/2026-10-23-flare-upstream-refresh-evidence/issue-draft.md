# Proposed issue: GLM host-cache row allocation remains a useful tuning candidate

Private draft; not submitted. Proposed title: **GLM host prefix cache: residual replay recomputation with a 32-GiB budget and fixed continuation-row split**.

## What we measured

Thank you for the recent cache-retention work in #38. We repeated our diagnostic workload on current main plus a separate minimal legacy-template compatibility fix, using the existing retention implementation and native metrics. We want to determine whether a configurable host row/page split would complement that work without changing the default.

Pinned setup: upstream `d5fd9824593c2a70f6bdc001b39f16b4c5b3b0b4` plus compatibility commit `0b21d7f4883ac6f2b099df55813e8be06777b9df`; `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD` revision `dec48abd33efa73c3bb7c95b74eee10cad34f9be`; two RTX PRO 6000 Blackwell Max-Q GPUs, TP2, configured C4 but serial requests, draft count 2 without adaptive speculation, balanced profile, 32-GiB host cache and disk cache disabled. Driver 615.71.10. The retained package graphs were not re-exported.

For each repetition we started a fresh server, ran three warmups, primed 52 fixed prompts in forward order and replayed them in reverse order, requesting one output token. Request bodies and prompt tokens were frozen. Request temperature zero is clamped by this package to its supported sampling range, so this is not described as greedy decoding.

| Repetition | Replay requests | Median first-generated-token TTFT | Host fetch completions | Native host `no_match` outcomes | Prefill tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 52 | 449.93 ms | 10 | 42 | 199,464 |
| 2 | 52 | 452.90 ms | 10 | 42 | 199,464 |
| 3 | 52 | 460.25 ms | 10 | 42 | 199,464 |

Every replay matched its priming output token, prompt-token sequence and body hash. Native `no_match` is a host-tier outcome and includes requests served by the GPU cache; it does not mean 42 requests recomputed their entire prompt. Per-request speculative work was not exposed and remains unknown.

The default allocator still gives 52 continuation rows and 7,314 page slots within the 32-GiB pool. Source inspection finds the quarter-budget row split in `SlabPool::sized_counts`. Current native telemetry does not expose the old instrumented row/page pressure counters, so this baseline establishes residual work and a repeatable tuning target, not causality from row pressure alone.

Protected warm controls also completed: 24 small-working-set replays at 9.382 ms median TTFT, and 12 page-heavy replays at 18.065 ms. Both recorded zero prefill and zero host fetches; they are different populations from the pressure workload and are not pooled. The full refresh contained 391 diagnostic requests plus one smoke across six fresh servers, with zero request failures. The incumbent was restored and verified afterward.

## Why investigate allocation separately

On historical source `47581cca`, a local startup option kept the 25% default and allowed 75% of the same budget for rows: 52 to 158 rows, with page capacity falling from 7,314 to 2,434. Three matched one-token reverse-replay comparisons showed 95.04–96.06% lower median first-token latency. Those results used driver 615.71.09 and older cache retention. They are historical motivation, not a measured current-main improvement.

That earlier experiment also failed a 128-token sampled-output equality gate and showed protected-workload regressions. We retain those failures. Exact state-copy checks cover only the declared bytes and cannot rule out omitted state. We are not proposing a default change or claiming numerical equivalence, general speedup, or serving-engine parity.

## Related upstream work and next test

- #38 already supplies retention, fallback, deferred-store/refill behavior and decision metrics; we do not propose duplicating it.
- #16 addresses shifted GLM draft-page capture. Our retained package has the old layout. Testing it needs re-export; it is neither proven nor ruled out as the cause of our sampled-output inconsistency.
- #35's requested-ID scores can help a numerical discriminator after re-export.
- #43 and #69 target different prefill/concurrency triggers from the zero-prefill warm controls; test them separately.
- #57's lifecycle histograms are useful but do not replace request-to-slot attribution. Issue #47 concerns a different GPU snapshot tier.

The next allocation experiment should compare current-main default 25% and an otherwise identical startup-only 75% candidate, including an unchanged 25% control for the patched build. Preserve physical budgeting and minimum capacities, use strict option validation and the existing shared CLI/Orbit path. Keep three matched 52-request repetitions, protected warm controls, complete failures and independent correctness gates. Re-export changes must be held constant across allocation arms. To attribute a result specifically to #16, use a matched export pair or component oracle rather than an old-versus-new package comparison.

A current 75% arm has not run. This issue can request maintainer feedback on the proposed control and workload; a performance PR should wait for that matched evidence. A separate, already tested compatibility PR fixes the retained GLM template load path.

Evidence: the accompanying public-safe report, reconstruction and raw artifacts will be attached for review. Internal operator paths and credentials do not belong in the upstream issue.
