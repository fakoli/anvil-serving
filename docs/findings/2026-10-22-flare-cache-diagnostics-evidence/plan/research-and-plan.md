# Flare cache diagnostics, 22 October 2026

Prospective focused diagnosis, not a serving ranking or promotion. Preserve wave-v1 and its hosted snapshot. Research performed on native Dark by Codex; no new runtime requests preceded this plan.

## What the research changes

The last 52-prompt workload completed three host stores per request, retained 52 host records, occupied 8.731 GiB of a configured 32 GiB host cache, and restored zero host tokens. Earlier smaller working sets did restore. Those observations are consistent with churn but do not prove broken keys or a defective eviction policy.

Flare 47581cca divides host allocation between fixed continuation-row and page pools. Free bytes are not interchangeable free rows. Its index evicts eligible leaves before considering ancestor-row compaction. Crucially, its three checkpoints are rendered message-prefix, full-prompt, and terminal consumed endpoints, not three prefill chunks. A terminal descendant cannot satisfy a shorter prompt, so blindly compacting ancestors may destroy the useful replay checkpoint. Read model-contract, core-design and owning offload sources before changing policy.

General serving-stack lessons: distinguish allocation capacity from live occupancy; measure admission, lookup, completed restore and reuse separately; preserve pinned/in-flight state; separate prefill, decode, scheduling and transfer costs; compare matched requests and runtime state before interpreting throughput. Paged allocation reduces fragmentation but does not automatically balance heterogeneous state pools.

Primary sources, observed 2026-10-22:

| Source | Date / pin | Applicability and decision |
|---|---|---|
| [vLLM prefix caching](https://docs.vllm.ai/en/latest/design/prefix_caching/) and [hybrid cache design](https://docs.vllm.ai/en/latest/design/hybrid_kv_cache_manager/) | Current documentation; hybrid article explicitly describes an older commit | Full prefix identity and compatible state across groups matter. Validate current implementation at 5957bbf125b1f6bed0dcbedba7737b27cd068f8d; no inference that GLM is a Mamba model. |
| [vLLM current cache manager](https://github.com/vllm-project/vllm/blob/5957bbf125b1f6bed0dcbedba7737b27cd068f8d/vllm/v1/core/single_type_kv_cache_manager.py) | 2026-10-22 | Current Mamba path searches compatible aligned checkpoints. Mechanism analogy only, not a drop-in GLM remedy. |
| [SGLang HiCache practices](https://docs.sglang.io/docs/advanced_features/hicache_best_practices) and [design](https://docs.sglang.io/docs/advanced_features/hicache_design) | Current docs | Host staging and completed movement have their own capacity/eligibility. Our disk tier is disabled; do not import L3 tuning formulas. |
| [SGLang component cache](https://github.com/sgl-project/sglang/blob/d0a75654368a470d8484e3ea45b06e500fee57af/python/sglang/srt/mem_cache/unified_cache/components/mamba.py) | 2026-10-22 | Component-specific LRU, host/device backup validity and pinned-state exclusions demonstrate why generic leaf eviction alone is not a sufficient hybrid policy specification. |
| [Marconi](https://arxiv.org/abs/2411.19379) | 2024-11-28, revised 2025-04-10; historical research | Selective checkpoint admission/retention is a candidate mechanism for hybrid-state caching. No published speedup is projected onto this hardware/model. |
| [PagedAttention](https://arxiv.org/abs/2309.06180), [SGLang](https://arxiv.org/abs/2312.07104), [Sarathi-Serve](https://arxiv.org/abs/2403.02310) | 2023–2024; historical mechanisms | Paging, prefix trees and chunked prefill solve distinct bottlenecks; phase-specific measurements precede kernel optimization. |
| [Flare upstream](https://github.com/NVIDIA/flare-local-inference) | Main 5f37bd14062334f7687d168d58ea00567b9aa7fe, 2026-10-21 | 73 commits beyond our pin; all three host-cache files unchanged. d2036807 reduces decode overhead; newer FP4 checkpoint support changes another axis. Retain as a separate future runtime arm, not an invisible baseline update. |

Exact downloaded sources, API comparison, pins and failed source lookups are retained under research/. Research-synthesis plugin 0.1.0+codex.20260913190724 was read. This is lead research plus independent plan/fixture review, not a three-answer research panel: the immediate falsifiable order probe is already specified by retained evidence; additional panel work cannot change that first test.

## Execution contract

Reuse instrumented image 47ce7fde (capture disabled), Flare 47581cca, package d5aec154, model dec48abd, TP2, fixed draft 2, host cache 32 GiB, disk disabled, same two GPUs at 300 W. Retain exact recipe bytes and verify current identity before taking down incumbent. Managed recipe lifecycle only. No route/model alias/provider changes, no upstream submission.

Window: at most 90 minutes from quiesce, stop experimental requests by minute 70, reserve 20 minutes for exact incumbent restoration and direct/routed validation. Abort new requests on identity drift, authentication, malformed transport, CUDA/Xid/OOM, containment or uncontrolled growth. Preserve natural output failures separately; no performance eligibility or quality acceptance is inferred from cache reuse.

1. Validate existing offline harness and new HTTP-only replay fixture. Snapshot incumbent, router, monitoring, GPU, rollback and configuration identities. Preview load/unload. Quiesce primary and drain before unload.
2. Fresh candidate incarnation A: verify identity without generation, then forward prime the exact retained 52 C1 prompt bodies, immediately reverse replay. Same max_tokens 2048, temperature 1, top_p .95, reasoning max. No unrelated generation requests between phases. Require active/pending/retiring and host transfer/waiting gauges zero, with stable counters across two observations; a 10-second drain timeout invalidates the phase. Report host lookup/hit/fetched/reused separately from GPU hits/reuse. Retain every body hash/order, usage, terminal/visible status, bounded response, per-request native counter deltas and timestamps. This is a cache probe; timings are descriptive.
3. Fresh candidate incarnation B: identical forward prime and forward replay. Counter resets and fresh host cache must be verified. This controls the replay-order hypothesis. One diagnostic pair per order is sufficient to nominate a cause, not to establish a performance gain.
4. If safe and time permits, replay the last 12 bodies twice on B as a small-working-set control. Do not call its non-fresh initial state comparable to the 52-prompt arms.
5. Interpret endpoint reuse before policy work. If reverse/small reuse succeeds, row/checkpoint churn remains a specific hypothesis. First add bounded row/page/eviction counters and a CPU reproducer; do not substitute a speculative compaction policy. If reverse fails, inspect key and checkpoint eligibility first. Follow-on policy tests require independent correctness and three matched repeats at fixed bytes with >=5% primary benefit and <=3% protected regression; no such claim is authorized by this diagnostic pair alone.
6. Complete three trace-off/trace-on repetitions at C1/C4 using the retained 1024-token fixture if time remains; freeze alternating order before requests. Nsight and kernel replay are later conditional stages, admitted only after cache effects and capture overhead are understood. No unsafe isolated replay of cooperative rank kernels.
7. Unload candidate; restore exact incumbent recipe and verify containment, monitoring registration, direct/routed preflight and readmission. Publish measured, failed and deferred cells distinctly, with the next causal experiment. Preserve hosted historical report; create a new dated report snapshot for these results.

The first window deliberately executes cache diagnosis before making any policy or kernel patch. A result-dependent second window may follow after offline implementation/review; the incumbent must be restored between them.
