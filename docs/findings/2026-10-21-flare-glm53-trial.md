# Flare GLM-5.3-Flash trial — 2026-10-21

**2026-10-21 follow-up and correction:** [The expanded optimization study](2026-10-21-flare-glm53-optimization.md) retains this original trial and shows materially better tuned Flare behavior. Its source review also corrects the capability attribution: the QAD checkpoint contains vision weights; the current Flare GLM runtime is text-only.

**Keep the incumbent; no promotion.** NVIDIA Flare completed a bounded managed trial and cached restart on two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs. Both recipes passed 32/32 natural-output responses and request canaries. The observed warm C4 results favor the incumbent. This is a comparison of whole recipes and different GLM checkpoints, not an isolated engine benchmark or full qualification.

The candidate was unloaded inside the authorized 60-minute GPU window. The exact retained incumbent container, image, recipe, revision and GPU selection were restored; all 14 protected manifest hashes matched. Direct and authenticated routed preflight passed 7/7 each and router admission resumed. No route remap or promotion occurred. [Restoration evidence](2026-10-21-flare-glm53-trial-evidence/restoration.json)

## Identity and workload

Flare source 47581ccac82660953a5b59cb04b2750d64edc97d used Local Inference Lab's GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD at dec48abd33efa73c3bb7c95b74eee10cad34f9be, TP2/C4/MTP3 with prefill chunk 4096. The incumbent is NVIDIA GLM-5.3-Flash-FP4-Local at 76348ddb2be2e35e39ecfc00e75dc07703f4f3d5, vLLM TP2/DCP2/C4/MTP3, batch 4096 and FP8 KV. Immutable image identities, complete launch flags and source hashes are retained in the [summary](2026-10-21-flare-glm53-trial-evidence/summary.json), [recipe](2026-10-21-flare-glm53-trial-evidence/configuration/serve-recipe.toml), and [source verification](2026-10-21-flare-glm53-trial-evidence/raw/benchmark-source-verification.json).

Each arm ran the same seven-check preflight and then 10,240/C1,10,240/C4,32,768/C1,32,768/C4, with eight requests per cell, temperature 1, top_p 0.95, seed 0, max_tokens 2048, response_words 0, observe policy, unique prefixes within the run and request canaries. These are nominal workload budgets; actual mean inputs were 7,680.875 and 22,921.625 tokens. Natural completion lengths vary. The repeated seed/request-index namespace made **both C4 cells 100% prompt-cache hits after C1**. C1 hit ratios were near zero. Thus the C4 results are warm-cache observations, not cold concurrency capacity. The same order does not make different cache implementations identical.

## Natural-output scout results

The [summary](2026-10-21-flare-glm53-trial-evidence/summary.json) is the authoritative derivative table. Values below are rounded from its native artifact metrics. All cells completed 8/8 with matching canaries. Decode is per-request p50; throughput is aggregate completion tokens per second. TTFO means first nonempty reasoning or content; visible TTFT means first content. Timing values below are milliseconds.

| Nominal context / C | Recipe | Mean output tokens | Decode p50 tok/s | Aggregate tok/s | TTFO p50 | Visible TTFT p50 | E2E p50 |
|---|---|---:|---:|---:|---:|---:|---:|
| 10,240 / C1 | flare | 272.0 | 192.5 | 125.6 | 768.6 | 1855.9 | 2107.2 |
| 10,240 / C1 | incumbent | 289.5 | 198.6 | 125.8 | 857.1 | 1824.6 | 2111.9 |
| 10,240 / C4 warm | flare | 265.9 | 71.0 | 276.1 | 28.9 | 2368.8 | 3187.5 |
| 10,240 / C4 warm | incumbent | 349.8 | 97.7 | 360.2 | 109.8 | 1383.4 | 2118.3 |
| 32,768 / C1 | flare | 224.8 | 192.4 | 63.6 | 2349.4 | 3052.5 | 3406.7 |
| 32,768 / C1 | incumbent | 285.2 | 197.8 | 71.9 | 2536.4 | 3509.1 | 3828.3 |
| 32,768 / C4 warm | flare | 265.6 | 69.6 | 270.5 | 40.5 | 2301.5 | 2777.7 |
| 32,768 / C4 warm | incumbent | 277.1 | 105.1 | 331.4 | 108.5 | 1635.0 | 2302.2 |

Flare C1 decode was about 3% lower in these observations. Warm C4 aggregate output throughput was 23.3% lower at the smaller input and 18.4% lower at the larger input. Flare emitted its first output sooner, but its warm C4 first visible content arrived later. Output lengths differ, so these are descriptive whole-recipe observations rather than equal-length decode speedups.

[Machine-derived chart pack](2026-10-21-flare-glm53-trial-evidence/graphs/matrix.svg) · [chart data and input hashes](2026-10-21-flare-glm53-trial-evidence/graphs/graph-data.json)

### Retained latency and prefill distributions

| Budget / C | Recipe | TTFO p50/p95 ms | Visible TTFT p50/p95 ms | TPOT p50/p95 ms | E2E p50/p95 ms | Effective prefill p50/p95 tok/s |
|---|---|---:|---:|---:|---:|---:|
| 10,240/C1 | flare | 768.6/776.4 | 1855.9/2800.8 | 5.2/5.7 | 2107.2/3089.9 | 9961.4/10053.7 |
| 10,240/C1 | incumbent | 857.1/860.8 | 1824.6/3077.7 | 5.0/5.2 | 2111.9/3348.1 | 8953.8/9027.3 |
| 10,240/C4 warm | flare | 28.9/31.6 | 2368.8/4627.1 | 14.0/14.4 | 3187.5/5482.9 | 263376.8/527652.9 |
| 10,240/C4 warm | incumbent | 109.8/180.1 | 1383.4/7441.9 | 10.2/11.6 | 2118.3/7744.7 | 65715.8/118350.3 |
| 32,768/C1 | flare | 2349.4/2427.6 | 3052.5/3758.2 | 5.1/5.8 | 3406.7/4076.2 | 9716.8/9951.3 |
| 32,768/C1 | incumbent | 2536.4/2560.6 | 3509.1/4572.2 | 5.0/5.2 | 3828.3/4890.3 | 9021.9/9252.6 |
| 32,768/C4 warm | flare | 40.5/57.1 | 2301.5/5056.2 | 14.1/14.6 | 2777.7/5454.6 | 524383.5/684587.8 |
| 32,768/C4 warm | incumbent | 108.5/162.0 | 1635.0/5344.4 | 9.3/11.6 | 2302.2/5783.5 | 210111.0/339120.0 |

Capacity-v3 TPOT and mean ITL are per-request aggregate proxies, not raw token-arrival distributions. Effective prefill includes queueing, scheduling, prefill and first-token work. Native files retain input/output distributions, confidence intervals, cache counters and observed client concurrency. With only eight samples per cell, p95 and especially p99 are descriptive; no stable service-level tail is claimed.

## Functional checks and negative results

Flare passed 7/7 preflight before the scout and7/7 after cached restart: smoke, JSON, retrieval, tools at C4, streaming tools, tool-result continuation and Responses subset. This is bounded protocol coverage, not general task quality. The strict 8K diagnostic requested exactly 128 words: Flare C1 produced 170/188/180/169 and C4 produced 140/147/168/126. Both were 0/4 performance-eligible; all responses and canaries passed. The incumbent strict diagnostics also yielded 0/4 eligible per C1/C4. Those failures are retained and excluded from performance ranking. Flare 32K strict cells were deferred to fit package-reuse testing inside the time budget.

No SWE, representative coding-quality, vision, MTP0, cold-C4, cancellation, endurance, full-context or real-client acceptance was completed. The Flare checkpoint is text-only and does not establish replacement of the incumbent vision contract. The initial incumbent diagnostic preceded the structured scout plan; no complete pre-request serving-comparison envelope exists. Accordingly the full comparison remains explicitly incomplete even though this bounded scout is closed.

## Preparation, resources and reuse

The first attempt failed cache admission before GPU use because the default inspection helper was absent. A managed explicit inspection-image option fixed it. The next attempt reached TP initialization but failed with `io_uring bounce initialization failed: Operation not permitted`. A candidate-only hash-bound seccomp profile adds the three required io_uring syscalls to a pinned Moby default; host policy remains unchanged. At two compile workers, r3 progressed too slowly for the trial budget. It was managed-unloaded and r4 continued with eight total workers, retaining completed caches. This is not a pure cold-start measurement.

R4 completed 511 tuned operators, 26 forward compilations in 371.2 seconds, 36 forward loads in 60.0 seconds and 54 graph captures in 10.2 seconds. Native export took 717.6 seconds. Preparation reached its 52 GiB cgroup cap with 7,463 max events and no observed OOM or OOM kill. A later cached native restart took**21.555 seconds**, used the exported runtime package, peaked around 2.32 GiB cgroup memory and passed 7/7 preflight. These startup observations are not a matched startup comparison against the incumbent.

Runtime reported 173.5 GiB GPU allocation and a 10.8 GiB KV pool with 3,118 pages of 512 slots: 1,596,416 allocated token slots across the pair. This is allocation, not measured simultaneous full-window capacity. Flare advertised 1,048,576 maximum context, versus 327,680 configured for the incumbent; the scout tested neither full window. Runtime GPU samples left roughly 2.6 GiB free per GPU, so a 3 GiB reserve gate is not established. Protected host/cgroup/GPU samples and native metrics are retained, including the preparation pressure.

The package index SHA-256 is `afbdc17bc85a906bb361ba3db92442e5b800c702f204ff0d3e00f7e5a5e94b50`, size 2,501,456 bytes. This hash covers the index only, not external weight blobs. Native signature verification and successful warm reuse are separate functional evidence. Model, kernel/package and private configuration volumes remain available; private signing-key material is excluded from all evidence and Git. Native logs retain public verification-key metadata.

## Future conversion and disposition

Retain the current primary. The [reconstruction packet](2026-10-21-flare-glm53-trial-evidence/configuration/README.md) preserves exact engine/model/image pins, Dockerfile, runtime config, recipe, seccomp provenance and package identity. The private operator record preserves selected GPU identities, local cache names, tested commands and exact rollback. Recipe support changes remain uncommitted and unreleased in the isolated source worktree; they must be reviewed and shipped before relying on the installed CLI for these fields.

A future conversion should freeze the matched finalist matrix first, resolve controlled-output suitability, separate cold and warm populations, add MTP-off controls, and pass independent quality, required modalities, context, cancellation and client acceptance. Promotion still requires a separate human decision. The full [evidence index](2026-10-21-flare-glm53-trial-evidence/README.md), [coverage gaps](2026-10-21-flare-glm53-trial-evidence/coverage-and-gaps.md) and [friction dispositions](2026-10-21-flare-glm53-trial-evidence/friction-log.md) retain failed attempts and operational follow-ups.
