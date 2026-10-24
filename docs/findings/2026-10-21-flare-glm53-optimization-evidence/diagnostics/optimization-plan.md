# Flare GLM optimization campaign

Observed 2026-10-21 on native Primary Linux. The user authorized experiments until 08:30 America/Los_Angeles today (15:30 UTC). Stop new experiments at 08:10 and reserve the final 20 minutes for exact incumbent restoration and reporting. This document is a plan; `campaign.json`, individual native artifacts, and the final report determine what actually ran.

## Objective and decision

Find whether Flare can improve useful local coding and conversation service on the existing two RTX PRO 6000 Max-Q GPUs. Rank correctness and successful task completion first, interactive latency second, concurrent useful throughput third, then memory/context and operational cost. A faster token counter is insufficient. Flare's current GLM recipe is text only, so it cannot replace the incumbent's vision contract without a separately qualified capability arrangement.

Preserve the incumbent, routes, cloud providers, checkpoint caches and previous failed evidence. No promotion is authorized. All GPU ownership changes use Anvil recipes/serves, with root as the sole operator. Reuse existing benchmark runners and upstream qualification tools; no new campaign framework.

## What the source says

The pinned source is `NVIDIA/flare-local-inference@47581ccac82660953a5b59cb04b2750d64edc97d`, checked out at `/workspace/flare-local-inference`. Ignition prepares model-specific operations with PyTorch, FlashInfer/b12x, compilation and CUDA graph capture. Helios schedules and replays the prepared graphs from Rust. Native serving cannot invent a missing graph shape: runtime flags can select or reduce prepared capacity, while expanding it requires preparation/export.

The first trial used `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD@dec48abd33efa73c3bb7c95b74eee10cad34f9be`, TP2/C4/MTP3 and prefill4096. It showed competitive C1 decode, earlier first reasoning output in some cells, and a roughly22-second cached restart. It did not establish an overall advantage. Its C4 cells were warm, its natural response lengths differed, and its checkpoint differs from the NVIDIA FP4 incumbent. Those results compare whole recipes, not engines in isolation.

The strongest concrete lead is the prepared speculative row envelope. `ignition/components/capacity.py:48-65` defaults to `max(padded_concurrency, maximum_width)`. C4/MTP3 therefore captures batch1 widths1–4, batch2 widths1–2, and batch4 width1. The retained preparation logs confirm those seven shapes. Capacity16 adds five shapes, allowing three-draft verification with four active requests. An independent source reviewer confirmed the mechanism; causality remains to be measured.

Other distinctions matter:

- Balanced and throughput policies select among existing graphs. Throughput relaxes delivery cadence preferences and can lengthen output gaps; it is not a free latency improvement.
- `ignition run` does not forward scheduler-policy. Managed native `flare run` permits explicit runtime controls without recompilation.
- Runtime MTP0 disables draft generation and state maintenance but retains draft allocations; it is a same-package execution control, not a memory baseline. `--no-mtp` changes package composition and draft memory and is a different experiment.
- GLM uses context parallelism; a TP2 logical page contains512 tokens, consistent with the retained metrics. Configured context, KV pool slots and measured simultaneous capacity are separate quantities.
- GLM history-copy drafts, packed verification and mixed-prefill are not supported recipe optimizations. Export/run currently accept the packed flag without implementing it; avoid that misleading path. Do not apply Qwen-specific flags by analogy.
- Extra graph capacities may consume more memory and reduce KV. The preparation estimate is fixed, so measure the actual peak and reserve.

## Measurement contract

Freeze source, image, checkpoint, GPU order, preparation flags, native flags, request parameters and output limits for each arm. Keep every failed attempt. Compare parent and candidate settings before requests. A changed checkpoint or precision is a new candidate, not an engine speedup.

Use the existing Anvil capacity runner for initial screening: 12 requests per cell, C1/C4, nominal contexts10240/32768, temperature1/top_p0.95, native thinking, max2048 generated tokens, request-specific canaries and natural summaries. Use distinct seeds for each context/concurrency cell, identical workload seeds across arms (the capacity runner does not set an API sampling seed), and immediately repeat each cell to measure warm prefixes. Record actual input/output tokens and cache counters. This is not an equal-output benchmark; use repeated trials, matched populations and latency distributions before making a causal performance claim. N<100 tail percentiles are descriptive only.

Warm graph-cost learning on separate request identifiers before measurement. Capture Prometheus and JSON metrics before/after each cell, especially round counts and GPU time by batch/verification width, offered/verified/accepted drafts, cache hits, active/pending requests, failures and KV state. Retain managed status, host-memory limits/events, GPU allocation and thermal/power observations. A high acceptance fraction without the batch/width denominator can conceal absent C4 speculation.

Use three levels when profiling is needed: complete GPU graph replay, native scheduler/replay, and HTTP service. Reuse the upstream tools where their contracts apply: `qualify.glm_performance` and `qualify.glm_gpu_analysis` provide a GLM batch-one path, while `qualify.gpu_replay` currently imports the Qwen3.8 bootstrap and cannot be run unchanged for GLM. A GLM multi-batch replay adaptation would need focused validation before measurement. Measure the whole graph across both TP ranks, including collectives and sampling. Do not use host launch time as GPU time or sum overlapping kernels as critical-path duration. Nsight binaries were not on PATH at initial discovery; record profiler availability rather than claiming a trace was collected.

## Ordered experiments

| Stage | Change | Falsifiable question | Proceed when |
|---|---|---|---|
| 0 | Fresh incumbent cold/warm baseline; original Flare native package | Can the original observations be reproduced with explicit cache populations? | Identity, containment, protocol and canaries pass |
| 1 | Capacity4 →16, keeping MTP3, balanced policy and all other settings | Do new batch4 widths2–4 execute and improve useful C4 service? | Full12-shape catalog, correctness and sufficient reserve |
| 2 | Same capacity16 package: balanced →throughput | Does scheduler choice help throughput enough to justify cadence/latency costs? | Repeated benefit on the same workload, no unexplained loss |
| 3 | Same package: adaptive3 →fixed3 and runtime MTP0 | Is speculation beneficial at C1/C2/C4, and is adaptive width choice appropriate? | Compare acceptance, round GPU time, emitted tokens and E2E together |
| 4 | If stage1 helps: MTP5/capacity24 | Does additional accepted depth amortize verification on this hardware? | Preparation fits; no degradation in quality or interactive service |
| 5 | If prefill dominates: chunk2048 versus4096 | Does smaller prefill reduce interference without unacceptable TTFO cost? | Real overlapping decode/prefill exposure is demonstrated |
| 6 | Profile only a demonstrated remaining limiter | Is cost in experts, CSF-scale work, collectives, KDA/MLA, scratch lifetime or scheduling? | A specific measured hotspot with a supported remedy |

Avoid a Cartesian sweep. Prefer cheap same-package controls, then new captured capacities, then code changes. Reject a branch when its paired data does not support it and move to the next useful question. New kernels require numerical/state parity and executable tests; never weaken rounding, scales, stream lifetimes or model semantics for throughput. GLM mHC scratch lifetime is an upstream investigation lead, not proof that aliasing is safe.

TP1 cannot hold this approximately178GB checkpoint on one96GB GPU. TP4 is unavailable here. Two full replicas are not a feasible placement for these weights. Different quantization/checkpoint/offload approaches are separate later candidates, with independent quality requirements.

## Quality and reliability

Run direct preflight for smoke, JSON,8K retrieval, concurrent tools, streaming tools, tool-result continuation and Responses. Use Flare-supported temperature1; its calibrated recipe does not accept the historical temperature0 default. Preserve any failure and investigate the actual layer.

Reuse the existing oracle8 agentic scout on both arms at identical supported sampling and budgets: planning, reasoning, structured edit, tool sequence, parallel tools, dependent result, recovery, debug loop and context recovery. Score fixture state and final output independently. These synthetic checks do not substitute for real repository work. If time permits, reuse the pinned SWE harness/assets for a bounded common issue subset, retaining attempted/graded/resolved counts, agent budgets and failures separately. Do not use Flare to judge Flare.

Expand a promising finalist to C2/C4 with ragged outputs, slot reuse, request cancellation and repeated prefixes. Test long-context retrieval progressively (32K,64K,128K, then the incumbent's intended range if feasible), preserving output/reasoning headroom. A configured1M limit does not prove1M quality or simultaneous capacity. For mixed load, verify real generated deltas from a long request before introducing fresh prefill, and correlate scheduler evidence; two open connections alone are insufficient. The existing stability runner assumes vLLM chat tokenization, so verify Flare compatibility before using it or state the coverage gap.

Keep strict repeated-word adherence failures separate from semantic coding quality. Do not erase earlier failures by relaxing their validator. A true no-MTP export is useful only after same-package controls demonstrate a memory or throughput reason to prepare it. Prefix-cache host/disk tiers should be tested later on multi-turn traces if GPU-cache churn is measured; their initial disabled state keeps the first comparison small.

## Selection, restoration and retained conversion path

An experiment is informative even if it does not qualify promotion. A prospective finalist should show a repeatable improvement of practical size (initial screening target10% in the relevant useful-throughput or latency metric), all deterministic acceptance checks, and no material regression in the protected workload. Repeat the parent after the candidate to expose drift; use confidence intervals or report insufficient samples. Do not claim a small percentage improvement from one noisy cell.

Abort new traffic on wrong identity, host OOM, GPU Xid, uncontrolled growth, containment failure or engine death. Capture owning logs and state before managed recovery. Keep candidate RAM at52GiB, zero swap, and a12GiB host reserve; do not change host power, clocks, drivers or networking. Stop new experiments by15:10 UTC even if a promising branch remains. Unload the exact candidate; use the frozen retained-incumbent transaction in `../2026-10-20-flare-trial/restore-commands.json`; require exact image/container identity, direct and authenticated routed preflight, GPU ownership and router readmission. Restore first, publish afterward if time is short.

Retain immutable recipe versions, package paths, existing index identity (which does not cover external weight blobs), image/checkpoint pins, request artifacts, failures and restoration evidence in this private campaign. Preserve the old trial. At closure, publish a sanitized dated finding and required benchmark matrix through the existing Anvil publication workflow. A later conversion requires a separate human decision, capability acceptance, supported manifest/route transition, rollback and real-client validation.

## Primary sources

- [Pinned Flare source](https://github.com/NVIDIA/flare-local-inference/tree/47581ccac82660953a5b59cb04b2750d64edc97d)
- [Capacity calculation](https://github.com/NVIDIA/flare-local-inference/blob/47581ccac82660953a5b59cb04b2750d64edc97d/ignition/components/capacity.py)
- [GLM recipe](https://github.com/NVIDIA/flare-local-inference/blob/47581ccac82660953a5b59cb04b2750d64edc97d/ignition/recipes/glm53/recipe.py)
- [Performance measurement contract](https://github.com/NVIDIA/flare-local-inference/blob/47581ccac82660953a5b59cb04b2750d64edc97d/docs/performance-work.md)
- [MTP design](https://github.com/NVIDIA/flare-local-inference/blob/47581ccac82660953a5b59cb04b2750d64edc97d/docs/mtp-design.md)

External LIL TP4 workstation reports are useful leads but are not measurements of this TP2 Max-Q system. SPEED-Bench can add diverse speculative-speed workloads; the earlier Anvil offline planner is not assumed to be an executable live benchmark lane.

## Follow-up decisions at11:41Z
- GPU cache churn was measured. Add a same-package memory-only32GiB prefix-cache arm after capacity tuning, with independent sizing review and live containment gates. Compare requested cache treatment to actual reuse.
- Capacity16 first preparation failed during compiled-forward warmup, after successful new-shape capture. Separate the compiled-forward namespace for the next version while retaining all lower-level caches, package signatures and binding checks. Keep the failed attempt.
- Reuse16-word strict visible-output cells as a distinct controlled population. Total reasoning/output tokens may still vary.
- Select a candidate before the fixed-five executable SWE run. The user deadline imposes a7200s outer bound versus historical21600s; record partial/attempted/graded/resolved counts and do not call this matched historical qualification. The task images and corrected client environment are already verified.
- Planned C1 context quality: needle and order,32K/128K/256K estimated buckets,10/50/90percentpositions,one repetition,8192output headroom. Actual prompt tokens determine measured coverage.

## Deadline review correction at12:48Z
The earlier7200s outer-bound description was incorrect: SWE applies timeout_s independently to agent and grader. No SWE was submitted under that assumption. The planned spec is reduced to3300s perstage and must bind supported absolute cancellation at15:05Z before submission. Preserve the prior unsubmitted spec as finalist-swe-spec-before-deadline-review.json. Stop and drain task/model work before15:10restoration.

## Effective timeout correction at13:00Z
Source review confirms the requested job timeout is not forwarded to SWE: the scout profile still supplies21600seconds independently per stage. The preceding3300-per-stage assumption was also incorrect. No SWE job was submitted. Detached-container custody and process-descendant cleanup also require a complete repair. Retain the fixed-five plan/assets, report0attempted/0graded with no quality score, and continue bounded model-native tests. See swe-blocked-final-assessment.json and the product ticket.
