# Request-to-evidence coverage

This matrix reconciles the campaign request with retained evidence. `Covered`
means the named bounded outcome has direct evidence; it does not imply
production qualification or promotion.

| Requested outcome | Required gate | Retained evidence | Status | Evidence boundary or next action |
|---|---|---|---|---|
| Research current configurations before testing | Dated sources with provenance and decision impact | [Source registry](source-registry.json) and [run plan](run-plan.json) | Covered | External results selected candidates; they were not treated as local measurements. |
| Test multiple Qwen3.8 27B artifacts and runtimes | Locally load and functionally probe each retained survivor | [Configuration](configuration.json), [evidence index](README.md), and per-arm preflights | Covered for the retained shortlist | Inferact, RadixArk, and kelnei/vLLM were measured; this is not an exhaustive artifact census. |
| Test context sizes, including the configured maximum | Retained successful requests with API-reported prompt usage | [Target-only 250K C1](nospec-250k-c1.json) and [DFlash2 K8 250K C1](dflash-k8-250k-c1.json) | Partial | Both planned 250,000 tokens, completed 1/1, and reported 241,153 actual prompt tokens against a 262,144 configured maximum. Near-limit evidence is single-request baseline coverage; the K12 finalist, RadixArk, kelnei/vLLM, TP2, and DP2 finalists were not all repeated near the limit. |
| Test concurrency and topology | Controlled cells across relevant concurrency and TP/DP choices | [Workload manifest](workload-manifest.json), [decision summary](summary.json), and native artifacts | Covered for C1/C2/C3/C4/C8 and aggregate C16 | DP2 won the bounded aggregate-throughput comparison; TP2 was rejected. Not every candidate was tested at every concurrency. |
| Tune draft depth, chunking, compilation, state slots, and speculation | Matched scouts followed by controlled finalists | [Run plan](run-plan.json) and [decision summary](summary.json) | Covered | Scout throughput selects finalists only; absolute scout and sustained-output results are not pooled. |
| Record TTFT, prefill, decode, TPOT, ITL, E2E, throughput, and percentiles | Request-level timing plus declared definitions and sample population | Headline N100 artifacts linked from the [evidence index](README.md) | Partial | TTFT, effective client-observed prefill, decode, TPOT/mean-ITL proxy, E2E, and aggregate throughput include mean/p50/p95/p99. Standalone prefill and raw token-arrival ITL distributions were not measured. |
| Make p99 and other percentiles interpretable | At least 100 measured requests for headline populations | [Decision summary](summary.json) | Covered for headline cells | N100 nearest-rank p99 is retained, but it is still one tail order statistic; smaller scouts keep descriptive percentiles only. |
| Use controlled output, request isolation, and explicit cache state | Fixed response target/headroom, unique canaries, and declared cache policy | [Workload manifest](workload-manifest.json) and headline artifacts | Covered | Every headline cell used the matched sustained-output contract and passed its own canary population. Shared-prefix experiments remain separate. |
| Compare speculative paths to no-spec controls | Otherwise matched target-only/no-spec evidence | SGLang baseline controls and [kelnei no-spec](kelnei-vllm0271-nospec-4k-c8-canary-long256-n100.json) versus [MTP2](kelnei-vllm0271-mtp2-4k-c8-canary-long256-n100.json) | Covered for published speculation claims | No-spec controls are configuration-family specific; results do not transfer between runtimes. |
| Build graphs from the benchmark evidence | Derive charts from retained numeric paths and preserve source hashes | [Graph manifest](graph-manifest.json), [graph data](benchmark-graph-data.json), and [SVG](benchmark-matrix.svg) | Covered | Raw native artifacts remain authoritative. |
| Keep the campaign repeatable and fix forward | Managed recipes, pinned identities, restoration, friction dispositions, and reusable workflow | [Configuration](configuration.json), [friction log](friction-log.md), [restoration](restoration.json), and [repeatable workflow](../../benchmarks/repeatable-campaigns.md) | Partial | Current evidence is reproducible, but durable multi-cell orchestration and cursor-based recipe logs are deferred. |
| Continue searching and testing automatically | Durable scheduled discovery plus resumable capacity campaign execution | None | Missing | Track the durable-job/logging product work separately; this completed campaign is not a continuous scheduler. |
| Establish broad behavioral qualification | Repeated quality, agentic, SWE, multimodal, routed/client, endurance, balancing, and failover gates | [Decision summary](summary.json) | Missing | The performance winner remains `no-promotion`. |
| Capture complete efficiency telemetry | Synchronized per-arm power, clock, temperature, host-memory, startup, compile, and energy/token evidence | [Friction log](friction-log.md) | Missing | No comparative energy or complete cold-start claim is allowed. |
| Restore the exact starting service and route | Managed restoration plus direct and routed acceptance | [Restoration](restoration.json) and linked smokes | Covered | Restoration passed after recorded validation friction; no promotion occurred. |
| Add reusable learning to the workflow | Evidence-linked skill/process update without copying raw session content | [Session efficiency review](session-efficiency.md) and [repeatable workflow](../../benchmarks/repeatable-campaigns.md) | Covered for workflow learning | Any separate engineering-learning entry is outside this evidence bundle and must be verified independently. |

## Metric interpretation

The campaign's `capacity-v3` TPOT and mean ITL values are aliases of the same
per-request mean interval. Their percentiles are across requests. They are not
token-level ITL percentiles. Effective prefill includes queueing, scheduling,
prompt processing, and first-token work.

The retained sustained-output cells share the requested 256-word/512-token
contract, but legacy artifacts do not prove exact word-count adherence. Their
completion caps and actual output counts are retained. New runs explicitly
record `observe` versus `strict` controlled-output policy; strict policy adds
an adherence gate. Legacy success is not retroactively upgraded to that gate.

DP2 timestamps support a bounded union window, not exact start alignment.
The corrected aggregate preserves both native replicas and labels missing
legacy configuration-fingerprint and clock-domain evidence explicitly.

At exactly 100 requests, nearest-rank p99 is useful bounded evidence but remains
sensitive to a single request. This campaign does not claim a stable production
service-level tail from one N100 population.
