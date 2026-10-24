# Flare GLM-5.3-Flash optimization — 2026-10-21

**Flare became a credible text-serving challenger after configuration tuning; retain the incumbent.** The initial “no benefit” interpretation was too broad. The tuned combination of wider prepared speculation graphs, fixed two-token drafts and a host prefix cache improved Flare's observed warm concurrent behavior. Results remain a bounded scout across different checkpoints, with unresolved quality and integration gates.

<!-- benchmark-result-card/v1 -->

## Local result card

Setup: two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs, native Linux, TP2/C4, direct online HTTP. Flare source `47581cca`, LIL QAD checkpoint `dec48abd`, speculative capacity 16, fixed two draft tokens from the MTP3-prepared package, balanced scheduler, 32 GiB host prefix cache, prefill 2048. [Managed recipe and reconstruction](2026-10-21-flare-glm53-optimization-evidence/configuration/README.md).

- Default-Max warm C4 natural-output aggregate throughput was **386/364 tokens/s** at nominal 10K/32K, versus **376/363** for the fresh incumbent. Each cell has 12 valid requests and approximately 100% prompt reuse. This is near parity in these observations, not an established speed advantage. The matched High-policy strict16 control favors the incumbent's observed completion latency.
- Default-Max warm C1 decode was **190/191 tokens/s**, versus **195/192** for the incumbent; each cell has 12 requests. Different generated lengths and checkpoints prevent an engine-only claim.
- Flare High reasoning passed **18/18 deterministic agentic cases**, **18/18 C1 context cases**, and **4/4 concurrent requests with approximately 317K actual input tokens each**. Its 96/96 natural responses establish completion and canary integrity, not semantic quality. The matched incumbent High agentic run passed15/18; its default Max run passed18/18.
- Both Flare and incumbent, at Max and High, each passed **100/100** strict16 requests at C4 and approximately 7.7K actual inputs. The 128-word test passed **0/12** for Flare Max, Flare High and incumbent Max/default; incumbent High was not tested in that population.
- In one mixed-load diagnostic, prefill 2048 reduced maximum observed decode-delivery gaps to approximately 244/266ms from 500/441ms at approximately 32.6K/101.6K fresh inputs. The counting oracle failed; this remains diagnostic timing evidence.

**Decision:** `no-promotion`. The current Flare GLM runtime is text-only; real repository coding, vision replacement, endurance, real-client acceptance, repeated finalist comparisons and a usable no-speculation control remain missing. [Evidence index](2026-10-21-flare-glm53-optimization-evidence/README.md) · [hashed manifest](2026-10-21-flare-glm53-optimization-evidence/artifact-manifest.json) · [authoritative summary](2026-10-21-flare-glm53-optimization-evidence/summary.json).

## Identity and measurement boundaries

Flare is [NVIDIA/flare-local-inference](https://github.com/NVIDIA/flare-local-inference/tree/47581ccac82660953a5b59cb04b2750d64edc97d), source `47581ccac82660953a5b59cb04b2750d64edc97d`. The checkpoint is `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD@dec48abd33efa73c3bb7c95b74eee10cad34f9be`. The pinned prepared image is `sha256:8908da0c34a446d7aa6a8a907e7cedc100a49a85a78848c624ba47ef64e21f37`; Flare 0.1.0 and Torch 2.14.1+cu132.

The incumbent is `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`, vLLM image `sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`, TP2/DCP2/C4/MTP3, FP8 KV, 327680 configured context and 4096 prefill batch. This comparison changes checkpoint, quantization and engine together. Both GPUs communicate over PCIe without NVLink; no other machine supplied the reported measurements.

Anvil's launcher was bound to source `80ad7e5c5de49221ba1e75fa133ef358af3e72e3` plus retained uncommitted trial support changes. These changes are tested source, not a released or deployed product update. The candidate served-name string still contains MTP3 across runtime overrides; exact recipe hashes, not that label, identify each arm.

Natural screening uses 12 requests per cell, C1/C4, nominal 10240/32768 input budgets, unique request prefixes, canaries, T1/top_p0.95, max2048 completion tokens, and immediate cold/warm repeats. Actual inputs are roughly 7.7K/22.9K; exact distributions are native evidence. Workload seeds match between arms; the capacity runner sends no API sampling seed, while Flare's default is 0. These are distinct prompts, not independent random-seed repetitions.

Cold/warm filenames record intended treatment; observed cache counters determine interpretation. Natural output lengths differ. Failed cells are excluded from performance ranking. Capacity-v4-reasoning distinguishes first reasoning-or-content output from first visible content. TPOT/mean ITL are per-request aggregate proxies, not raw token-arrival distributions. Twelve-sample p95/p99 values are descriptive. Even the 100-sample nearest-rank p99 is a single tail order statistic, not a stable service-level bound.

The common campaign templates were initialized after early requests. No complete pre-request, content-bound serving-comparison matrix exists. Per-cell snapshots are not continuous telemetry. **Full serving comparison incomplete** is an explicit methodological limitation, not a claim that the recorded requests did not run.

## What changed and what the evidence shows

Ignition prepares model operations and CUDA graphs; Helios schedules their native replay. Runtime flags cannot create missing prepared shapes. The default C4/MTP3 row capacity4 contains seven shapes: batch1 widths1–4, batch2 widths1–2, and batch4 width1. Thus four active requests cannot use the full intended speculative width. Capacity16 adds five shapes, and native graph counters confirm wider batch4 verification executed.

Capacity16 alone did not solve cache churn. The GPU snapshot pool repeatedly evicted prefixes; a 32 GiB memory-only host tier restored full reuse on the small 12-request warm working set. An independent pressure fixture also observed a real 7288-token host restoration and four correct concurrent branch answers. Its punctuation and counter-oracle failures remain retained. A 24 GiB arm also held this small working set, but that does not establish a general minimum cache size.

Fixed2 balanced scheduling provided a useful compromise in the tested natural workloads. Fixed1 and throughput scheduling did not provide a consistent advantage. Fixed3 had a deterministic agentic failure. There is no valid no-speculation performance control, so none of these changes establishes a causal speculative-decoding speedup.

The 2048 prefill package exposes 12 speculative shapes and 51 captured graphs. It reduced the largest observed mixed-load delivery gaps in one pair, while the 101K prefill itself grew from 18.0s to 21.3s. The two prompts differ by 3 tokens and generated trajectories differ; the counting validator also failed. This supports another controlled experiment, not a universal latency claim.

## Screening and quality evidence

[All capacity cells](2026-10-21-flare-glm53-optimization-evidence/diagnostics/screening-table.json) and [native quality results](2026-10-21-flare-glm53-optimization-evidence/diagnostics/native-quality-table.json) are the authoritative full tables. The machine-derived [chart pack](2026-10-21-flare-glm53-optimization-evidence/graphs/matrix.svg) and [source-bound chart data](2026-10-21-flare-glm53-optimization-evidence/graphs/graph-data.json) compare separately labeled recipes and request policies.

| Arm | Natural completion/canaries | Deterministic agentic | C1 context |
|---|---:|---:|---:|
| Fresh incumbent / Max |96/96|18/18|18/18 through approximately 317K|
| Fresh incumbent / High |96/96|15/18|Not run|
| Original Flare capacity4 |95/96|18/18|Not run|
| Capacity16 adaptive3, host cache disabled |95/96|18/18|18/18 through approximately 260K|
| Capacity16 fixed3, host cache disabled |96/96|17/18|Not run|
| Capacity16 adaptive3, host32 |96/96|18/18|Not run|
| Capacity16 fixed2, host32, prefill4096 |96/96|18/18|15/18 through approximately 317K|
| Capacity16 fixed2, host32, prefill 2048, Max |96/96|17/18|18/18 through approximately 317K|
| Same 2048 runtime, High policy |96/96|18/18|18/18 through approximately 317K|

The p2048 Max agentic debug-loop failure began with invalid `scope=all`; subsequent calls were rejected and the final answer correctly acknowledged that tests had not run. High passed that fixture, but one policy pass does not erase the Max failure or establish broad coding quality.

High produced substantially shorter reasoning on the natural workload: warm C1 visible TTFT around0.18/0.20s and E2E around0.52/0.51s. This is a request-policy/output-length tradeoff, not engine acceleration. Matched incumbent High also completed96/96 natural requests, with warm C1 visible TTFT around0.16/0.28s and E2E0.50/0.59s. Its deterministic agentic result was15/18: two structured-edit final-answer failures and one debug-loop protocol failure. Default incumbent passed18/18. These small populations do not justify globally reducing reasoning. Low failed the initial needle preflight; three successful identical replays at the default seed do not erase that failure.

The two C4 long-context batches each passed 4/4 at 317257–317277 actual tokens, with sampled active max 4, client overlap, complete telemetry, no engine-failure delta, and clean drain. Default Max took approximately 175s; High approximately 163s. Each batch is one bounded observation. Active4 does not prove simultaneous GPU computation, four full-context KV residencies, sustained capacity, or 1M-context correctness.

### Larger strict16 populations

These use the same 100 workload seeds/request indices, C4, roughly 7.7K actual inputs, T1/top_p0.95, max2048 total completion tokens and exactly 16 visible words. Completion counts include reasoning. All four populations were 100/100 valid; the High result follows much shorter reasoning. They are whole-recipe observations with different checkpoints, not engine-only speedups. Nearest-rank p99 at 100 samples remains one tail order statistic.

| Recipe / policy | Wall time s | E2E p50 / p99 s | Mean completion tokens | Cached input fraction |
|---|---:|---:|---:|---:|
| Incumbent / Max default | 152.8 | 5.887 / 8.381 | 175.90 | 0.000% |
| Incumbent / High | 111.9 | 4.392 / 5.939 | 69.87 | 0.000% |
| Flare / Max | 178.9 | 6.984 / 10.580 | 220.20 | 0.112% |
| Flare / High | 119.9 | 4.753 / 6.811 | 60.16 | 0.112% |

## Negative results and unrun gates

- **Exact output:** 128-word strict cells failed under host24, p2048 Max and p2048 High. Max produced 133–198 words in the p2048 cell; High 156 or more, with the largest bounded capture containing 1634 words and failing the completeness/canary gate. Shorter strict16 passes are a separate population. The matching incumbent control also passed 0/12, with eleven count failures and one truncated capture. This does not identify a Flare-specific defect.
- **MTP0 first fault:** the first C4 tool probe aborted in rank0 sampling with status3/error1; peer disagreement followed. Error1 combines non-finite maximum, invalid mass, FlashInfer invalidity and out-of-vocabulary conditions. The exact retry passed seven protocol checks, but the cause remains unproven. No MTP0 timing control was accepted.
- **Context:** the 4096 fixed2 host32 arm passed 15/18; two answers exhausted their 8192-token budget without visible content, and one returned the wrong marker. Later 2048/High passes do not erase those failures.
- **SWE:** five pinned tasks were selected, but attempted 0, graded 0, resolved=null. Source review found that the requested job timeout was not propagated and detached Docker cleanup was not bounded. The tracked ticket `.tickets/2026-10-21-swe-deadline-and-container-custody.md` records the required repair. No model coding score is inferred.
- **Instrumentation:** no Nsight/CUPTI trace, kernel modification, calibrated hardware critical path, repeated randomized finalist matrix, or sustained service tail was measured.
- **Capabilities:** no Flare vision or real-client replacement was qualified. Correction to the earlier trial: the checkpoint contains vision weights; the current Flare GLM runtime is text-only.

## Resources, reproducibility and next experiments

The 2048 package reports 171.9 GiB GPU allocation, 3630 logical 512-token pages, or 1858560 allocated KV slots. These are runtime allocation values, not measured full-window capacity. Host32 provides 7314 pages and 52 snapshot rows. The package index SHA256 is `0fdced23da87f99c3669dc05ab87ecd7f17a6572773c6b61021d077f68a09ed9`; its 2645178 bytes do not cover external weight blobs. Stored-data verification and package loading are separately retained.

The successful 2048 preparation used eight workers, a separate forward-cache namespace and 677.8s export. Preparation reached its 52 GiB cgroup ceiling with 33296 max events and no observed OOM/swap; runtime memory remained below the configured gate in retained checks. A failed earlier capacity 16 preparation retained its BindingMismatch evidence. A fresh forward namespace recovered preparation; the precise cache-identity cause remains unproven.

Retain both 4096 and 2048 fixed2 recipes, the adaptive host-cache controls, exact prepared packages, model/image/source pins and the protected rollback transaction. Private signing keys and credentials must remain excluded. The [source study and ordered plan](2026-10-21-flare-glm53-optimization-evidence/diagnostics/optimization-plan.md) maps broad options to supported code paths and feasibility.

Next work should first repair bounded SWE execution and obtain a usable no-MTP control. Then freeze a new comparison before requests, repeat parent/candidate/parent with independent seeds and representative traces, measure useful completed tasks and latency, and profile only the demonstrated bottleneck. Test longer mixed conversations and cache pressure before choosing 24 versus 32 GiB. MTP5/capacity24 requires a new feasibility check and package; GLM-inapplicable Qwen flags are excluded. TP1 cannot hold this checkpoint in 96 GiB and TP4 is unavailable here.

## Restoration and decision

The exact retained incumbent container and image were restored. Direct and authenticated routed preflight each passed 7/7; the primary route was ready and admitting at15:07UTC. The candidate was absent and all 14 protected manifest/rollback hashes matched. [Restoration proof](2026-10-21-flare-glm53-optimization-evidence/restoration.json). An all-tier status also reported an unavailable secondary tier, whose prior state was not established in this campaign; primary restoration is not a fleet-health claim.

Keep the incumbent. Promotion, client migration, model capability changes and release of the trial-support source remain separate decisions. No route remapping or automatic promotion is part of this scout.
