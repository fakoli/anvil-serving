# Flare cache diagnostics: row pressure and replay order

**Date:** 22 October 2026. **Decision:** no-promotion. This is a completed internal diagnostic experiment, not a new serving-performance ranking.

<!-- benchmark-result-card/v1 -->
## Result card

| Item | Local result |
| --- | --- |
| Outcome | The host cache exhausted its fixed state-row pool while most page slots remained free. This identifies a concrete allocator experiment; no optimization gain is claimed. |
| Setup | Local Inference Lab GLM-5.3-Flash NVFP4/MXFP8 CSF-QAD, Flare 47581cca, TP2, fixed two draft tokens/MTP3, balanced scheduler, host cache 32 GiB, disk disabled. Two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs, native Linux, 300 W each. |
| Measurement | Direct online endpoint, serial cache requests; fresh 52-prompt prime followed by reverse or forward replay. Same corpus repeated in the existing scheduler-instrumented image and a counter-only derivative. [Managed reconstruction recipe](2026-10-22-flare-cache-diagnostics-evidence/receipts/counter-arm/flare-instrumented-recipe.toml). |
| Completed population | 440/440 cache requests produced visible responses. Separately, 35/35 decode fixture requests completed exactly 1,024 output tokens, including five warmups. Visible completion is not semantic-quality acceptance. |
| Replay result | Both reverse arms: 8 full GPU hits and 5 host restores out of 52. Both forward arms: 0 host restores and 0 full GPU hits. |
| Allocator evidence | 52 state rows, zero free rows at steady state; at least 7,059 of 7,314 page slots free. Counter runs had row-pressure evictions and zero page-pressure events. |
| Main limitation | No allocator change, causal trace-overhead estimate, new kernel critical-path capture, semantic qualification or parity claim. Different checkpoints prevent engine-only comparison with the incumbent. |

[Evidence index](2026-10-22-flare-cache-diagnostics-evidence/README.md) · [manifest](2026-10-22-flare-cache-diagnostics-evidence/artifact-manifest.json) · [authoritative summary](2026-10-22-flare-cache-diagnostics-evidence/summary.json) · [restoration](2026-10-22-flare-cache-diagnostics-evidence/restoration.json).

## Research changed the experiment

The [previous wave](2026-10-21-flare-inference-diagnostics.md) showed zero host reuse across a 52-prompt workload, three stores per request, and roughly 8.73 GiB resident under a configured 32 GiB budget. The immediate question was whether cache identity/restore was broken or whether the workload was evicting useful checkpoints before reuse.

General serving-stack design separates allocated capacity, occupancy, admission, lookup, completed restore and actual reused tokens. Prefill, decode, scheduling and transfer costs need separate observations. A paged allocator reduces fragmentation, but different kinds of state can still exhaust independently.

| Primary source | Relevant mechanism | Consequence for this experiment |
| --- | --- | --- |
| [vLLM prefix caching](https://docs.vllm.ai/en/latest/design/prefix_caching/) | Prefix identity and reusable cache blocks | Keep exact request bodies and inspect native reuse counters; a completed prime is not proof of a cache hit. |
| [vLLM hybrid cache design](https://docs.vllm.ai/en/latest/design/hybrid_kv_cache_manager/) and [current pinned manager](https://github.com/vllm-project/vllm/blob/5957bbf125b1f6bed0dcbedba7737b27cd068f8d/vllm/v1/core/single_type_kv_cache_manager.py) | Different state groups require compatible checkpoints and allocation rules | Account separately for page slabs and state rows. The design article describes an older commit, so current source was checked. |
| [SGLang HiCache](https://docs.sglang.io/docs/advanced_features/hicache_design) and [best practices](https://docs.sglang.io/docs/advanced_features/hicache_best_practices) | Host staging, transfer completion and storage tiers have separate constraints | Observe completed restores, pending transfers and host capacity. Disk is disabled here; L3 guidance is not a measured local remedy. |
| [SGLang component cache](https://github.com/sgl-project/sglang/blob/d0a75654368a470d8484e3ea45b06e500fee57af/python/sglang/srt/mem_cache/unified_cache/components/mamba.py) | Component-specific eviction, pinning and valid host/device backups | Preserve state eligibility and in-flight safety before changing retention policy. |
| [PagedAttention](https://arxiv.org/abs/2309.06180), [SGLang](https://arxiv.org/abs/2312.07104), [Sarathi-Serve](https://arxiv.org/abs/2403.02310), [Marconi](https://arxiv.org/abs/2411.19379) | Paging, prefix trees, chunked prefill and selective state retention address different problems | Use these as historical mechanism priors, not projected speedups on this hardware. |

The Mamba-specific source is a state-management analogy. It does not establish that this GLM model is Mamba or that another engine's policy can be transplanted unchanged. Research dates, pins, applicability and decision impact are retained in the [source registry](2026-10-22-flare-cache-diagnostics-evidence/source-registry.json) and [prospective plan](2026-10-22-flare-cache-diagnostics-evidence/plan/research-and-plan.md).

Flare upstream main was 5f37bd14062334f7687d168d58ea00567b9aa7fe on 21 October, 73 commits beyond our pin when checked. The examined host index, memory store and slab allocator files were unchanged. Upstream d2036807 addresses decode overhead; it is a separate future runtime arm. New checkpoint support is another variable and was not silently folded into this experiment. Local Inference Lab's b12x repository was archived at the observed pin; source availability is not evidence of current maintenance or local performance.

## Exact setup and retained inputs

The product checkout is 80ad7e5c5de49221ba1e75fa133ef358af3e72e3 with retained local trial support. Flare source is 47581ccac82660953a5b59cb04b2750d64edc97d. The checkpoint is `local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD@dec48abd33efa73c3bb7c95b74eee10cad34f9be`; exported package is d5aec1543dc32413175462d17d374e54d27def24129f878045358c4db128c357. Served name is `glm53-flare-47581cca-tp2-c4-mtp3`.

The first image is sha256:47ce7fde879d731aca62cd2c9004f04e2e689498d04fae09261fcb654db8fa51. The counter derivative is sha256:aa74f31ae8a97387ecf14af5b334222ba858ca540241d45a9eccdbae0054c0fc. It adds observations and tests, not eviction-policy or kernel changes. The [counter patch](2026-10-22-flare-cache-diagnostics-evidence/receipts/cache-counters.patch.gz) binds that local change. Driver is 615.71.09, topology PCIe TP2, with 53,248 MiB host-memory containment, zero additional swap and a 12,288 MiB host reserve.

Cache probes use the exact previous 52 C1 prompts: 7,675–7,708 actual input tokens, maximum 2,048 completion tokens, temperature 1, top-p 0.95 and Max reasoning. C1 describes offered serial requests; the serve permits C4, capacity 16 and prefill 2,048. A 32,768-token workload ceiling is not the runtime's advertised 1,048,576-token context or measured full-context capacity. Startup allocation reports 1,858,560 KV token slots and 32 GPU snapshot slots; those are allocated resources, not concurrent capacity results.

Each phase retains exact body/order hashes, responses, events, usage, native counters and incarnation identity. Fresh arms verify empty host pools and zero generation counters. Between requests the fixture waits for idle request/transfer gauges and stable counters. The runner rejects identity changes, restarts, containment/OOM errors, counter resets, incomplete populations and mismatched replay bodies. A bounded outage admits requests until minute 70 and reserves restoration through minute 90. No unrelated generation is inserted between prime and replay.

## Cache results

The [summary](2026-10-22-flare-cache-diagnostics-evidence/summary.json) is derived from all retained requests, with a SHA-256 ledger over consumed artifacts. The two counter arms reproduce the first image's order effect exactly at this level of observation.

| Phase | Requests / visible | Full GPU hits | Requests with host restore | Prefill tokens, native counter delta |
| --- | ---: | ---: | ---: | ---: |
| A prime | 52/52 | 0 | 0 | 399,440 |
| A reverse | 52/52 | 8 | 5 | 299,383 |
| B prime | 52/52 | 0 | 0 | 399,440 |
| B forward | 52/52 | 0 | 0 | 399,431 |
| B small prime | 12/12 | 0 | 3 | 18 |
| B small repeat | 12/12 | 0 | 5 | 14 |
| C counter prime | 52/52 | 0 | 0 | 399,440 |
| C counter reverse | 52/52 | 8 | 5 | 299,383 |
| D counter prime | 52/52 | 0 | 0 | 399,440 |
| D counter forward | 52/52 | 0 | 0 | 399,431 |

Both reverse passes fully reuse GPU state for indices 51–44, restore host state for 43–39, then recompute older entries. Forward replay reuses only the nine-token common GPU prefix for each request. The small control is **not fresh**: it starts after B forward and retains that state. Its few prefill tokens show extensive reuse, although none qualifies as a full-prompt GPU hit under the exact counter definition.

| Counter-image phase | Row-pressure iterations | Evicted index records | Page-pressure iterations | Compacted rows |
| --- | ---: | ---: | ---: | ---: |
| C prime | 105 | 105 | 0 | 0 |
| C reverse | 130 | 130 | 0 | 0 |
| D prime | 105 | 105 | 0 | 0 |
| D forward | 156 | 156 | 0 | 0 |

Counts are per allocation/eviction loop, not request counts; evicted records can include pruned rowless ancestors. Row/page pressure can overlap in the general case. No-eligible-victim and oversized-request counters stayed zero here. At each large phase's end, 52 rows are occupied and 7,059 of 7,314 pages remain free: only 255 page slabs are occupied. Resident host-record bytes are 9,374,826,496, or **8.731 GiB**, not 9.37 GiB. Configured budget, physical pool allocation and resident payload bytes are different quantities.

This establishes that the row subpool constrained this image and workload while pages retained substantial headroom. It does not prove the split explains every miss or that reallocating bytes will improve throughput. Reverse success also rules out a universally nonfunctional host restore path for these requests.

## Code-level explanation and contribution candidates

In pinned Helios `offload/slab.rs`, `Pool::sized_counts` allocates roughly one quarter of the byte budget to fixed state-row records and the remainder to page records, subject to minimums. Free page capacity cannot automatically become a free row. `offload/memory.rs` asks the index for eligible victims when either pool lacks the required slots. The new counters report those branches before the existing decisions, preserving their behavior.

The three stored checkpoints are the rendered message-prefix endpoint, full-prompt endpoint and terminal consumed endpoint. They are **not three prefill chunks**. A terminal descendant does not satisfy a shorter prompt, so deleting ancestor rows indiscriminately can remove the useful replay checkpoint. The CPU reproducer uses a simplified, ready, unpinned three-endpoint history with 52 rows; it reproduces forward/reverse churn but does not model the entire asynchronous GPU lifecycle.

The smallest next performance candidate is a different **row/page partition at the same total 32 GiB**, informed by exact layout record sizes and checkpoint demand. Preserve indexing, pinning, eligibility, transfer completion and all generation settings. Test the same replay probes plus fresh small and page-heavy/long-context protected workloads. Require correctness, three matched repetitions, at least 5% primary benefit and no more than 3% protected regression before accepting a tune. The [next experiment plan](2026-10-22-flare-cache-diagnostics-evidence/next-experiments.md) makes those gates explicit.

The observability patch and minimal reproducer are already concrete contribution candidates. The allocator patch is still a proposal. Neither has been submitted upstream, and the existing serving recommendation is unchanged.

## Decode trace controls: useful capture, unresolved causality

There were 14 cells: C1/C4 warmup followed by three alternating trace-off/on pairs at each concurrency. All 35 requests generated exactly 1,024 tokens from 7,037-token prompts. The 30 non-warmup requests fully reused their prompts. Paired bodies match, including seed 42, temperature 1, top-p 0.95 and top-k 20.

Nevertheless, **all 15 individual request pairs produced different token sequences**, with different speculative work. The accepted seed field does not establish deterministic sampling, and this observation alone is not a seed-parser defect. Trace-on versus trace-off median last-token-minus-first-token time changed by −13.08%, +3.91%, +1.97% at C1 and −0.38%, −6.54%, −0.69% at C4. These are descriptive values in the retained summary, not causal instrumentation-overhead or speedup estimates.

Six one-second captures retained 371 scheduler samples across both GPU ranks with zero drops: 82/81/80 for C1 and 44/42/42 for C4. Graphs were `speculative1x3` and `speculative4x3`. GPU intervals overlap; their sum is not a critical path, and wall time minus GPU time is not pure CPU overhead. Physical scheduler slots are not HTTP request IDs. No new Nsight Systems critical-path capture or Nsight Compute hardware-counter experiment completed in this wave.

Flare's implementation path remains Ignition for Python/PyTorch graph and operation preparation and Helios for Rust scheduling/replay, using the pinned b12x/CuTe kernel stack mapped in the prior report. We identified these implementation tools; no evidence identifies a particular AI authoring tool used by upstream developers.

## Instrumentation, failures and repeatability

Ten new host-pool metrics expose total/free rows and pages plus page pressure, row pressure, evictions, compaction, unavailable victims and oversize rejection. Dynamic monitoring registered each fresh counter incarnation; all ten series were verified under its own Prometheus generation label. They are queryable through the existing Grafana data source. This wave did not add a dedicated cache dashboard panel. Generic monitoring acceptance is separate from browser-render acceptance and from releasing the trial CLI/controller changes to the fleet.

The managed image build passed 37 applicable CPU tests and a release build. The broader test attempt retained 95 passes, 21 `io_uring` permission failures and eight ignored tests. Disk/composite runtime behavior is not qualified; the live recipe explicitly disables disk. An initial compile failure placed fields on the wrong struct and was fixed before any load. The pinned build environment supplied missing host toolchain dependencies without installing host packages.

Independent review tightened the analysis and publication gates: exact phase inventories, hash-bound originals, strict timestamp domains, negative/nonfinite/duplicate counters, raw/summary agreement and explicit restoration receipts. The analyzer's 17 checks include three valid controls and 14 damaged inputs rejected. The [friction record](2026-10-22-flare-cache-diagnostics-evidence/friction.md) preserves orchestration mistakes and fixes instead of erasing failed attempts.

The existing kernel-tuning skill now covers separate row/page accounting, request-order probes, matching decoded work before overhead attribution and retaining the incumbent container's warm filesystem state. The CLI, private recipe registry and observability configuration already supply the operational home; a new plugin would duplicate them.

## Restoration and remaining limits

The first window restored the exact incumbent recipe and image after container reconstruction, which repeated cold autotuning. Its conservative closure record is 50.88 minutes. The second window preserved that container and restored the same ID; all final checks completed within 28.513 minutes of quiescing. Both are within the 90-minute bound. Candidate absence, incumbent image/checkpoint, containment, zero OOM, router readiness/admission, protected configuration hashes and unchanged 300 W limits were verified. Direct and routed preflights each passed 7/7 groups with 20/20 tool calls; the final monitoring verifier passed 20/20 checks.

The second restore's outer command wrapper expired after 120 seconds while the retained model was still loading normally. That CLI operation did not return success. The same container continued startup; exact protocol readiness, managed monitoring synchronization and fresh acceptance checks completed afterward. The failure and recovery receipts are retained. An initial monitoring check ran before scrape catch-up and failed six checks; the subsequent complete check passed, with the failed attempt preserved. Future orchestration must allow the managed readiness deadline plus overhead rather than truncate its wait.

The common publication ledger was consolidated at closure; prospective research, fixture, order and counter-arm plans were separately retained before their respective requests. This is not a retrospectively frozen serving comparison. The [coverage ledger](2026-10-22-flare-cache-diagnostics-evidence/coverage-and-gaps.md) keeps semantic quality, strict output, coding, full-context capacity, endurance, no-speculation comparison, real-client replacement and kernel attribution outside the completed claims.

Every cache/decode request remains in the compressed evidence archives because order and per-request counter transitions are necessary to audit the diagnosis. A subset would lose the eviction/replay sequence. The private Tailscale report is not an immutable anonymous public artifact store. Public copies pseudonymize operator identities and paths; provenance maps original hashes to sanitized payloads. Evidence retention does not authorize promotion, upstream submission or fleet deployment.
