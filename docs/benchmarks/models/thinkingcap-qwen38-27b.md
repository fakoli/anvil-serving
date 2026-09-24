# ThinkingCap Qwen3.8 27B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** User-selected vision-capable secondary profile with complete local weights.
    - **Selected or best-qualified configuration:** 128K/C1 Triton + MTP3, BF16 KV and generic UVA 3 GiB weight offload; promoted through the existing secondary alias.
    - **Measured hardware:** One RTX 5090, 32,607 MiB, Windows/Docker Desktop/WSL2.
    - **Evidence:** 27/27 functional, 9/9 thinking observations, 18/18 vision, 30/30 limited quality, strict capacity 100/100, context 60/60 all normal stop, and synthetic agentic 2/2.
    - **Decision:** `current`; human-authorized router promotion is recorded in the [separate deployment finding](../../findings/2026-09-24-thinkingcap-secondary-promotion.md). Fleet acceptance is tracked there.
    - **Important limitation:** Separate small-image and near-limit text tests; no combined near-limit vision, C2, SWE, video, or production soak qualification.
    - **Review dates:** evidence cutoff and latest review 2026-09-24.

### Review narrative

The user required at least 128K after the earlier 32K profile passed its bounded contract. Generic UVA offload makes room for a larger BF16 cache while retaining MTP3 and the complete vision-capable checkpoint. The retained recipe passed all declared function, vision, reasoning, repeated quality, strict capacity, full context and synthetic agentic gates. Thirty near-limit context cases used 125,838–125,869 actual input tokens with a 4,096-token completion allowance.

The earlier 32K profile remains the qualified rollback and faster small-context alternative. Those qualification campaigns did not change routes. A subsequent [human-authorized promotion](../../findings/2026-09-24-thinkingcap-secondary-promotion.md) records the routed deployment separately.

### Why this configuration was selected

The selection answers three requirements together: complete downloadable local weights, working vision, and at least 128K total context on the actual 32 GB RTX 5090. The AWQ checkpoint still uses NVFP4 W4A4 for supported language weights; “AWQ” here does not mean that the vision tower or every tensor has that precision. The cached snapshot contains 26 files and 23,447,947,228 bytes, including the vision and MTP components. The [original identity and gate summary](../../findings/2026-09-23-thinkingcap-evidence/summary.json) binds that observation to the pinned revision.

This is a capacity and feature choice with a substantial latency cost. On the retained short-output workload, the selected 128K recipe has about 6.59 times the median TTFT and 0.328 times the median decode rate of the qualified 32K recipe. Both passed their declared correctness gates. The evidence supports keeping 128K for the requested context requirement and preserving 32K as the faster alternative; it does not establish that this checkpoint is the best model for every task or isolate an offload or MTP effect. [Exact comparison and ratios](../../findings/2026-09-24-thinkingcap-context-evidence/capacity-summary.json).

## Immutable identity

- **Model:** `bottlecapai/ThinkingCap-Qwen3.8-27B-NVFP4A4-AWQ@f8fe157f207a13f977bc3d620ce10a3e9ba5ab11`, served as `thinkingcap-awq-5090`.
- **Runtime:** vLLM 0.29.0, image digest `082ca6f035279109041ffd3fe0695cb568b29bc580b35c4f297a66a08b216c1b`, engine revision `98dff2a81d747d1dba01a47f939f48c3526d4206`.
- **Current recipe:** [128K identity](../../findings/2026-09-24-thinkingcap-context-evidence/identity-mtp-uva3.json), semantic digest `c81bf6a3c79e6062c99258243d50a1c6feaa0886c8ff6724f79b2cca8f46f026`.
- **Launcher:** operational CLI 1.2.1 at repository revision `2b99e3353eb2e5a2813195af85050b1da0754332`, with retained source hashes; documentation is authored in a separate worktree.

## Tested hardware and topology

- **Measured:** One RTX 5090, sm_120, driver 616.92; direct managed loopback endpoint, one active request.
- **Current startup:** 19.11 GiB GPU weights, 3.02 GiB generic UVA offload including 0.86 GiB visual, 9.7 GiB KV cache, 138,519 reported cache tokens and 0.10 GiB CUDA graphs.
- **Host RAM:** UVA uses pinned system RAM. After guarded idle cache reclaim and all model gates, Windows had 7.01 GiB physical and 9.04 GiB virtual free. Minimum sampled GPU free memory was 1,440 MiB across 1,561 retained samples, above the 1,024 MiB floor. The continuous series covers the full context matrix; a separate final GPU observation follows the later agentic gate. [Resource details](../../findings/2026-09-24-thinkingcap-context-evidence/final-resource-summary.json).
- **Ending state:** [campaign restoration record](../../findings/2026-09-24-thinkingcap-context-evidence/restoration.json). Qualification-era state; subsequent promotion is recorded separately.

## Engine, quantization, KV, context, and concurrency recipe

- **Retained 128K recipe:** [exact MTP3/UVA3 snapshot](../../findings/2026-09-24-thinkingcap-context-evidence/configurations/thinkingcap-awq-vllm029-5090-128k-mtp3-bf16-uva3.toml). Triton target/draft, NVFP4 W4A4 AWQ mixed FP8/BF16 weights, BF16 KV, model-default FP32 recurrent state, MTP3, graphs, memory fraction .93 and 1,024 batched tokens.
- **Contract:** 131,072 total prompt plus output tokens, C1; configured maximum two images at about 1 MP each, zero video. The image gate uses 640 x 360 fixtures. Exact-total/one-over admission and combined near-limit images were not tested.
- **Quality budget:** 10,240 total completion tokens, expressed as 8,192 reasoning headroom plus 2,048 visible tokens; the endpoint does not hard-partition these channels.
- **Context method:** 4,096-token completion allowance and 1,024-token calibration margin; actual endpoint usage is reported separately from requested buckets.
- **Qualified 32K alternative:** [32K BF16 snapshot](../../findings/2026-09-23-thinkingcap-evidence/configurations/thinkingcap-awq-vllm029-5090-32k-mtp3-triton-bf16.toml), GPU-resident weights and memory fraction .87.

Recipe files retain exact launch-time annotations. Qualification status belongs to the linked findings and native results.

## Evidence by measurement class

### Short-output capacity

- **Status:** Complete matched workload populations for two whole recipes.
- **Measured:** 128K n=100 strict: 3,003.0448 ms median TTFT, 51.885477 tok/s median decode, 3,852.9263 ms median E2E, 11.678388 tok/s aggregate throughput. Earlier 32K: 455.4421 ms TTFT and 158.355179 tok/s decode.
- **Limits:** Nominal 4K/C1, actual 3,609-3,687 input and 45 output tokens; 32 words plus canary, 512-token cap, warm process, prefix cache off, thinking off. Context, placement and memory fraction differ together. No isolated causal claim or generalization to long input, reasoning, images or concurrency. TPOT is a per-request proxy; n=100 p99 is not a stable SLA.
- **Evidence:** [native capacity](../../findings/2026-09-24-thinkingcap-context-evidence/native/mtp-uva3/capacity.json), [comparison](../../findings/2026-09-24-thinkingcap-context-evidence/capacity-summary.json), [charts](../../findings/2026-09-24-thinkingcap-context-evidence/benchmark-matrix.svg).

### Optimization history and rejected branches

**Status:** The table retains the measured progression, including failures. All capacity rows use the pinned checkpoint and runtime on the same RTX 5090, nominal 4K/C1, unique prompts and canaries, strict 32-word output, thinking disabled, and 100 attempted requests. Rows are complete serving configurations, with differences in attention, context, cache and placement; they are not a matched experiment isolating one setting.

| Configuration | Strict capacity | Median TTFT (ms) | Median decode (tok/s) | Aggregate output (tok/s) | Disposition |
|---|---:|---:|---:|---:|---|
| 16K eager | 100/100 | 482.385 | 15.673 | 13.630 | Initial capacity control |
| 16K CUDA graphs | 100/100 | 397.105 | 67.868 | 42.968 | Superseded; repeated quality 27/30 with budget exhaustion |
| 16K Flash-target MTP3 | 99/100 | 433.573* | 152.925* | Ineligible | Strict output failure; repeated quality 27/30 |
| 16K Triton MTP3, BF16 KV | 100/100 | 453.653 | 158.496 | 61.420 | Advanced to 32K |
| Triton MTP3, FP8 KV | 100/100 | 494.953 | 137.991 | 55.171 | Rejected after quality 27/30; three reasoning-budget exhaustions |
| 32K Triton MTP3, BF16 KV | 100/100 | 455.442 | 158.355 | 61.146 | Qualified faster alternative |
| 128K Triton MTP3, BF16 KV, UVA3 | 100/100 | 3,003.045 | 51.885 | 11.678 | Qualified and subsequently promoted |

\* Flash-target medians describe the 99 valid requests only; the failed population is excluded from clean performance comparisons. **Evidence:** [original campaign summary and exact native paths](../../findings/2026-09-23-thinkingcap-evidence/summary.json), [original chart](../../findings/2026-09-23-thinkingcap-evidence/benchmark-matrix.svg), and [128K comparison](../../findings/2026-09-24-thinkingcap-context-evidence/capacity-summary.json).

The GPU-only 128K attempt failed its startup reservation before loading weights, so it never produced a capacity measurement. The selective vision-offload/no-MTP 128K branch loaded and passed the initial function, vision, reasoning and context scout, but three normally terminated wrong answers reduced repeated quality to 27/30. Its broader capacity and context runs were deliberately pruned. Neither failed branch is a competitive speed result. [128K branch evidence](../../findings/2026-09-24-thinkingcap-context-evidence/summary.json).

For the selected 128K population, p95 TTFT was 3,043.131 ms, p95 E2E 3,894.420 ms, and median/p95 derived TPOT 19.273/19.420 ms. These distributions include only the specified short-output workload and are not reasoning latency, long-context throughput, or a production SLA. **Limits:** no matched Triton no-speculation control and no BF16-weight reference were completed; quantization-quality equivalence and an isolated speculative-decoding speedup remain unproven.

### Function, vision and limited quality

- **Status:** Current MTP3/UVA3 gates passed.
- **Measured:** 27/27 functional, 9/9 thinking-enabled observations, 18/18 image attempts and 30/30 on ten fixed MMLU-Pro validation questions repeated three times, all normal `stop`.
- **Limits:** Synthetic small images and a small repeated diagnostic; not a full MMLU-Pro score, broad quality ranking, or BF16-weight equivalence proof.
- **Evidence:** [function](../../findings/2026-09-24-thinkingcap-context-evidence/native/mtp-uva3/functional.json), [reasoning](../../findings/2026-09-24-thinkingcap-context-evidence/native/mtp-uva3/reasoning.json), [vision](../../findings/2026-09-24-thinkingcap-context-evidence/native/mtp-uva3/vision.json), [quality](../../findings/2026-09-24-thinkingcap-context-evidence/quality-summary.json).

### Context and agentic behavior

- **Status:** Full context 60/60 correct, completed and normal stop; synthetic agentic 2/2 passed.
- **Measured:** Thirty full-matrix cases used 32,658–32,689 actual input tokens and thirty used 125,838–125,869, with a 4,096-token completion allowance. Five task types, three positions and two repetitions were covered at each depth. Agentic evidence contains six API turns and four tool calls. The earlier near-limit scout took 157.64 seconds end to end; this is one diagnostic observation.
- **Limits:** Requested buckets and cache counts are not measured input depth. Separate text-context and small-image results do not qualify their combined boundary. No C2, SWE or production soak result.
- **Evidence:** [full context](../../findings/2026-09-24-thinkingcap-context-evidence/context-full-summary.json), [agentic](../../findings/2026-09-24-thinkingcap-context-evidence/agentic-summary.json), [current scout](../../findings/2026-09-24-thinkingcap-context-evidence/context-scout-summary.json), [coverage](../../findings/2026-09-24-thinkingcap-context-evidence/coverage-and-gaps.md). Prior 32K separately passed [60/60 context](../../findings/2026-09-23-thinkingcap-evidence/context-result-summary.json) and [2/2 synthetic agentic cases](../../findings/2026-09-23-thinkingcap-evidence/agentic-result-summary.json).

## Decision and promotion state

`current` in the dated [September 24 promotion record](../../findings/2026-09-24-thinkingcap-secondary-promotion.md). The user authorized the router and fleet update after qualification. Initial routed C1 gates passed; final-output multimodal retries passed after two retained relay failures. Pi effort compatibility required correction. The exact qualified 32K alternative remains preserved; fleet and UI acceptance are reported independently in the promotion record.

### Client and monitoring acceptance

| Surface | Retained result | Boundary |
|---|---|---|
| Router `llm.secondary` | Exact selected model, 131,072 total context, 10,240 maximum output, C1, two images | Total context includes both input and output; near-126K text tests reserved 4,096 output tokens |
| Pi | Normal installed Linux and Windows clients completed a real tool/result conversation at the user's `high` setting | Explicit per-model mapping sends supported `xhigh`; routing still selects the same alias and model |
| Managed client catalogs | Discovered Pi/OpenClaw/Hermes catalogs across four hosts carry the final limits; Companion reconciliation is idempotent | Final metadata refresh and previously retained client smokes are separate evidence |
| Grafana / Prometheus | Persisted secondary panels and live context, loaded, image-limit and scrape-health metrics verified | Detailed remote-engine throughput telemetry remains a separate observer gap |
| Open WebUI | Persistent router provider and authenticated in-container model catalog verified | Browser chat acceptance was not completed |

The output limit was raised from the initial 4,096 to 10,240 because three successful quality attempts exceeded 4,096 completion tokens. The Pi rejection was an upstream unsupported-effort error, corrected by a capability-derived client map; it was not evidence of routing to a different machine. These fixes and the two retained intermittent image relay failures are covered in the [promotion finding and raw evidence](../../findings/2026-09-24-thinkingcap-secondary-promotion.md).

## Failures and gotchas

- **128K reservation failure:** GPU-only memory fraction .95 failed before weight loading. This is not a model/KV-fit result.
- **Rejected no-MTP 128K branch:** Selective visual UVA passed function, vision, reasoning and context scout but failed repeated quality 27/30. Three wrong answers stopped normally; this was not budget exhaustion. Capacity and full context were pruned.
- **Earlier campaign:** Flash-target MTP was performance-ineligible at 99/100 strict adherence. FP8 KV failed quality 27/30 with three reasoning-budget exhaustions. These observations do not prove a general effect of MTP or cache precision.
- **Tooling and repository boundaries:** Managed combined long-context vision testing is unavailable. Durable outer job wrappers require a canonical evidence companion for inspection. The earlier full repository gate retained an unrelated Windows Pi RPC failure (7,923 passed, 417 skipped, one failed); no runtime or test patch remains.

## Dated run history

- 2026-09-24 - [Secondary promotion](../../findings/2026-09-24-thinkingcap-secondary-promotion.md), user-authorized 131072/10240/C1/two-image router contract.

- 2026-09-24 - [128K context extension and system-RAM tradeoff](../../findings/2026-09-24-thinkingcap-context-5090.md), qualified within the declared bounded contract and retained.
- 2026-09-24 - [Earlier qualified 32K profile](../../findings/2026-09-23-thinkingcap-5090.md), retained faster alternative and rollback.
