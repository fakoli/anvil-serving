# Flare inference diagnostics: wave 1

**Date:** 2026-10-21–22. **Decision:** no-promotion; exact incumbent restored. This wave is diagnostic evidence, not serving qualification.

<!-- benchmark-result-card/v1 -->
## Result card

| Setup | Value |
| --- | --- |
| Model | Flare: Local Inference Lab GLM-5.3-Flash NVFP4/MXFP8 CSF-QAD at dec48abd; incumbent: NVIDIA GLM-5.3-Flash FP4-Local at76348ddb. Full revisions below. |
| Hardware | 2x RTX PRO6000 Blackwell Max-Q96GB, native Linux, PCIe TP2, driver615.71.09, 300W per GPU. |
| Runtime | Flare47581cca, fixed2 draft tokens/MTP3, capacity16/C4, balanced policy, host cache32GiB, prefill2048. Incumbent TP2/DCP2/MTP3, FP8 KV. |
| Managed recipes | [Unchanged Flare](2026-10-21-flare-inference-diagnostics-evidence/configuration/flare-control-recipe.toml), [instrumented Flare](2026-10-21-flare-inference-diagnostics-evidence/configuration/flare-instrumented-recipe.toml); sanitized reconstruction records. |
| Measurement path | Direct online endpoints; one priming block then three measured repetitions at each C1/C4. Priming does not establish reuse. |
| Contract | Nominal input10,240, approximately7.7K actual; output cap2,048; Max reasoning, T1/top-p0.95, natural output. Configured workload ceiling32,768, not full-context capacity. |
| Evidence | Functional/capacity/compatibility observations;32blocks,3392attempted including priming; two visible-output failures, both incumbent C4. Trace pilot complete; broader profiling partial. |
| Decision | No overall performance ranking, promotion or upstream submission. Exact incumbent container/image restored and primary readmitted. |

Artifact [manifest](2026-10-21-flare-inference-diagnostics-evidence/artifact-manifest.json), [evidence index](2026-10-21-flare-inference-diagnostics-evidence/README.md), [authoritative summary](2026-10-21-flare-inference-diagnostics-evidence/summary.json) and [provenance](2026-10-21-flare-inference-diagnostics-evidence/provenance.json).

[Machine-derived chart](2026-10-21-flare-inference-diagnostics-evidence/graphs/matrix.svg) · [chart data](2026-10-21-flare-inference-diagnostics-evidence/graphs/graph-data.json). Charts compare Flare executable images with capture off; they do not measure tracing overhead.

Status: measured wave closed, 2026-10-21–22. All 32 planned capacity blocks finished: 3,390/3,392 requests produced visible output, including priming; measured repetitions were 2,542/2,544. Two incumbent C4 requests exhausted their reasoning budgets. Exact incumbent restoration and final monitoring passed. Documentation validation and independent review are retained in the private closure record; the evidence manifest binds the public artifacts.

## Purpose and decision rules

The purpose is to locate actionable code or model-runtime bottlenecks and make diagnostics repeatable. This wave does not authorize promotion or upstream submission. Both RTX PRO 6000 Blackwell Max-Q GPUs remain at 300 W; power is observed, not changed or assumed to explain a difference.

The [execution plan](2026-10-21-flare-inference-diagnostics-evidence/plan/execution-plan.md), [interpretation contract](2026-10-21-flare-inference-diagnostics-evidence/plan/analysis-contract-v2.md), [coverage ledger](2026-10-21-flare-inference-diagnostics-evidence/plan/coverage-and-gaps.md), and [next experiments](2026-10-21-flare-inference-diagnostics-evidence/plan/next-experiments.md) are the working records. The coverage and friction ledgers were consolidated during execution. They were not frozen before the first request, and this is not a complete `serving-comparison/v1` qualification.

## What was built and exercised

Flare now has a local scheduler-trace instrumentation patch. Its bounded capture retains graph identity, physical scheduler slots, selection/commit/completion-observation timing, prior shape-cost observations, both GPU-rank event intervals, loss accounting and clock anchors. The patch is retained in [flare-instrumentation.patch](2026-10-21-flare-inference-diagnostics-evidence/configuration/flare-instrumentation.patch.gz), with [source identity](2026-10-21-flare-inference-diagnostics-evidence/configuration/flare-patch-identity.json) and [build result](2026-10-21-flare-inference-diagnostics-evidence/configuration/image-build-result.json). It is an observability contribution candidate; no performance patch or upstream submission has been made.

Anvil's trial checkout supplies `models recipes scheduler-trace --config ... --output ... --confirm` and capacity configuration files. Protected key-file handling, recipe mounts, authenticated model identity probes and monitoring generation fences are tested source changes. The observer configuration is deployed locally. These generic Anvil changes have not been released to the normal native fleet CLI/controller.

Monitoring registered the control and instrumented incarnations and verified local model, engine, GPU and dashboard identity after scrape catch-up. Stale registration was rejected. A separately owned remote engine blocked two checks during candidate admission; those failed/blocked receipts remain retained. The final restored-state verifier passed all 20 checks. This is API/PromQL dashboard acceptance, not browser-render verification. Raw snapshots, continuous Prometheus history and two-second host CPU/memory/pressure/I/O samples are retained privately. There is no continuous cgroup sampler; per-block runtime checks retain containment and OOM state.

The existing kernel-tuning skill now requires a complete authenticated live pilot before longer trace use. Four reviewed prose decision cases cover missing authentication, complete live capture, non-applicable offline tuners and dropped samples. These are skill review cases, not proof of fleet installation. A new plugin is unnecessary: the existing CLI, observability owner and skills already provide the right home for the workflow.

## Experiment and its limits

The order is incumbent-before, unchanged Flare, instrumented Flare with detailed capture off, and incumbent-after. Each arm uses C1 and C4, one full priming block and three measured repetitions: 52 requests per C1 block and 160 per C4 block. Prompts are approximately 7.7K actual tokens, with a nominal 10,240 input budget, maximum 2,048 completion tokens, temperature 1, top-p 0.95 and Max reasoning. Workload seeds bind prompt content and order; they are not forwarded as API sampling seeds.

This is a natural-output observational workload. Output lengths vary, and there is no semantic or request-canary oracle. Priming is not proof of cache reuse. Per-request TPOT is a duration/token-count proxy, not raw token-arrival jitter. C1 block p99 at 52 samples is descriptive. Three repetitions do not supply a robust service-tail guarantee. Flare and the incumbent also use different checkpoints, quantization and serving stacks, preventing engine-only attribution.

The unchanged and instrumented Flare arms use the same exported model package and runtime settings, but different executable images and source. Their capture-off comparison can detect a conspicuous compatibility or performance change; it is not a causal measurement of capture overhead. That requires the separately planned fixed-token trace-off/on repetitions within the instrumented image.

## Measured results

Mean block aggregate completion throughput across three repetitions; TTFT and TPOT columns are the range of the three block medians in milliseconds. These are natural outputs, not fixed-token decode rates. Failed-arm timing distributions describe survivors only and do not support a ranking. All priming blocks passed and are excluded here.

| Arm | C | Visible successes / attempted | Mean block tok/s | TTFT p50 range, ms | TPOT p50 range, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| incumbent-before | 1 | 156/156 | 118.881 | 1914.1–2060.3 | 5.2–5.3 |
| incumbent-before | 4 | 479/480 | withheld | 6956.7–7172.5 | 11.0–11.5 |
| flare-control | 1 | 156/156 | 114.408 | 2011.6–2189.5 | 5.4–5.4 |
| flare-control | 4 | 480/480 | 146.660 | 6692.4–6918.5 | 13.8–14.1 |
| flare-instrumented | 1 | 156/156 | 114.037 | 2011.7–2115.1 | 5.4–5.4 |
| flare-instrumented | 4 | 480/480 | 145.910 | 6785.5–6872.5 | 13.8–14.3 |
| incumbent-after | 1 | 156/156 | 117.223 | 1984.3–2018.5 | 5.3–5.3 |
| incumbent-after | 4 | 479/480 | withheld | 6988.7–7373.2 | 10.9–11.0 |

Each Flare arm completed636/636 measured requests; neither proves semantic quality. The unchanged/instrumented capture-OFF C1 rates are114.408/114.037tok/s and C4 rates146.660/145.910tok/s. Different executable images and unrandomized order prevent a causal overhead claim. All per-request records, TTFO, TTFT, E2E, output lengths, effective prefill and TPOT distributions remain in the native artifacts.

GPU history covers all24measured windows, both GPUs and11metrics, with no missing metric-window combinations. The configured limit stayed300W on each device. Sampled temperatures ranged45–89°C across the full wave; Flare windows ranged84–88°C. Returned power samples can momentarily exceed the configured limit. Window membership uses Prometheus evaluation timestamps; collector lag can affect boundary samples. Repeated scrape values are not independent samples; these observations do not explain a performance difference or measure energy. See [GPU observations](2026-10-21-flare-inference-diagnostics-evidence/diagnostics/gpu-observations.json).

## Diagnostic findings

### Host-cache working-set behavior is the strongest current lead

Across all eight unchanged-Flare blocks, counters reconcile with the client populations. Each request reuses only nine prompt tokens, host-cache restored tokens remain zero, and three host stores occur per request. The host tier ends at 52 records and approximately 8.73 GiB resident under a 32 GiB budget. This contrasts with the earlier successful 12-request working set. It does not prove the cache is universally broken.

Pinned source allocates a fixed row/page split; row exhaustion can trigger eviction even when pages remain, and eligible leaf eviction precedes some ancestor-row compaction. Missing free-row/page and host-eviction-reason counters prevent causal attribution. GPU snapshot evictions must not be described as host evictions. The [hash-bound native analysis](2026-10-21-flare-inference-diagnostics-evidence/diagnostics/flare-control-native-analysis-v2.json.gz) retains all blocks, and [cache units](2026-10-21-flare-inference-diagnostics-evidence/diagnostics/cache-units.json) correct the earlier GB/GiB confusion.

The smallest next experiment is to prime the same 52 prompt bodies in forward order and replay immediately in reverse order, without intervening traffic. Then add bounded row/page-pressure and eviction-reason counters and a CPU cache-index reproducer before changing policy. Compare a single policy change at the same total byte budget, with a small-working-set and page-heavy protected lane. A performance patch needs the declared 5% primary benefit, no more than 3% protected regression, correctness and repetition.

### The scheduler trace is usable, but does not yet identify the limiter

The one-second C4 pilot retained 44 complete samples from both ranks with zero drops while four requests each generated exactly 1,024 tokens from 7,037-token prompts. All sampled graphs were unqueued `speculative4x3`. Median maximum-rank GPU interval was 22.683 ms, median host wall interval 22.729 ms, and median rank-interval spread 4.736 microseconds. The [analysis](2026-10-21-flare-inference-diagnostics-evidence/diagnostics/scheduler-pilot-analysis-v2.json) retains distributions and clock bounds.

Similar rank intervals do not exclude a stall common to both ranks. Wall minus GPU time is not pure CPU overhead. `retained_ns` is a prior shape-cost observation, not the submitted plan's estimate. Request handles are physical slots/generations, not HTTP request IDs, and graph dimensions are capacities rather than actual token counts. Only time-window overlap can currently connect native and client records.

### Kernel work needs a more focused next capture

Earlier package/module mapping nominates the routed `W4A16FusedMoeKernel` in Local Inference Lab's b12x/CuTe DSL stack. Overlapping summed kernel durations are not critical-path percentages. No new Nsight Systems node capture, hardware-counter interval or Nsight Compute replay has completed in this wave. Cooperative TP kernels require a numerically checked component reproducer and supported replay method before isolated profiling.

Flare's build path is now mapped: Ignition prepares operations/graphs using Python, PyTorch and the pinned kernel stack; Helios is the Rust scheduler/replay runtime. The earlier private mapping record pins b12x revision622cec10eb75da189f4dadddabc2d142a33cd211 and CuTe DSL4.7.1 with PyTorch2.14.1+cu132. Its summed kernel-time observations are prior evidence, not a new measurement in this wave. This identifies the implementation tools, not an undocumented authoring agent.

## Failures and deferred work

The incumbent-before C4 r1 population had one reasoning-only length exhaustion: 2,048 completion tokens, no visible answer, and a terminal length event. No corresponding engine crash was observed; the bounded authoritative engine logs and no-OOM/container-identity checks are retained. The original failed request and denominator remain retained. The incumbent-after C4 r3 also failed once: request76 exhausted2,048 reasoning tokens with638reasoning chunks and no visible answer. Both C4 control arms and the overall ranking are ineligible; successful survivors are not substituted into a winning score.

Extended context/cache sweeps were deferred prospectively after that failure. Additional trace repetitions and new profiler windows are deferred to preserve the original 90-minute outage bound and 20-minute restoration reserve. The valid C4 pilot does not fill these missing lanes. Quality, coding, vision, full-context capacity, endurance, real-client replacement and a valid no-speculation comparison remain outside this wave's completed evidence.

## Closure

The managed candidate was removed and the exact retained incumbent container/image restored. Direct and routed preflights each passed7/7 with20/20tools. Original router/topology and six discovery-file hashes are unchanged; the authorized protected monitoring key reference remains installed. Final idle health, router readmission, incumbent GPU-process ownership, no OOM and300W limits passed. The outage was72.342minutes, within the original90-minute bound. All16incumbent native request/token populations reconcile, including failed-response usage. The final failed block ended the measurement runner with exit1 after retaining all raw outputs; fresh final checks replace its skipped post-block identity check. No request was retried. No promotion, upstream submission or fleet CLI/controller release occurred.

## Exact identities and reuse

The [summary identity](2026-10-21-flare-inference-diagnostics-evidence/summary.json) retains full checkpoint, package and image digests. Flare source is47581ccac82660953a5b59cb04b2750d64edc97d; the local instrumentation patch isSHA256a049239165bfebc3f3f9d19f51d62bce01a0c7cd1762602507cb0d55807ce337. The control image is a540b65396a83b8b5148bbb51e7ee62cb16498ecee679433017673053b1b9854 and instrumented image47ce7fde879d731aca62cd2c9004f04e2e689498d04fae09261fcb654db8fa51. The model package isd5aec1543dc32413175462d17d374e54d27def24129f878045358c4db128c357. The served candidate name isglm53-flare-47581cca-tp2-c4-mtp3; the incumbent isglm53-nvidia-fp4-local-tp2-327k-c4-mtp3-r2. Full recipe/config/source files are linked from the evidence index.

The candidate advertises1,048,576 native context tokens, but this wave uses a32,768 configured workload ceiling and approximately7.7K actual input. The allocated KV pool and cache slots are configuration observations, not measured simultaneous capacity. The image has53,248MiB host-memory containment with no swap and a12,288MiB host reserve.

Publication note (2026-10-24): this campaign used the trial-checkout diagnostics,
monitoring hooks and kernel-tuning skill changes described above. Their live
capture and monitoring additions are not part of the released fleet CLI; see
[instrumentation availability](../benchmarks/engine-testing.md#instrumentation-and-source-availability)
before repeating the experiment. The next experiment is specified in the retained
[next-test plan](2026-10-21-flare-inference-diagnostics-evidence/plan/next-experiments.md).
Use [operator skill discovery](../OPERATOR-SKILLS-AND-SUBAGENTS.md) for the supported
qualification and publication workflows. No new plugin is needed. Fleet release
and any upstream patch submission remain separate actions.
