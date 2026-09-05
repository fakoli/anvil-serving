# Qwen3.8 27B RTX PRO 6000 comprehensive optimization campaign

**Campaign date:** 2026-09-04 local time; retained runs continued into
2026-09-05 UTC.

**Decision:** two independent SGLang TP1 replicas are the bounded aggregate-
throughput winner; retain one optimized TP1 recipe and the RadixArk lower-TTFT
tradeoff; reject TP2 on this PCIe/WSL2 host; kelnei/vLLM MTP2 beats its exact
no-spec control but not the SGLang finalist; **no promotion**.

## Result card

<!-- benchmark-result-card/v1 -->

| Field | Local result |
|---|---|
| Models | Inferact and RadixArk Qwen3.8-27B NVFP4 targets; incoai DFlash2 draft; kelnei integrated-MTP NVFP4 target |
| Hardware | Two NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition GPUs, 97,887 MiB each, SM120, PCIe PXB, no NVLink, Docker Desktop/WSL2 |
| Runtimes | SGLang source `5f55db35` in digest `616a3e97`; vLLM 0.27.1 digest `c2f3b1b9` |
| Selected SGLang recipe | Inferact NVFP4, DFlash2 K12, 1,024-token chunks, FP8 target KV, BF16 Mamba state, `extra_buffer`, 96 state slots, 262,144 context, C8, thinking disabled |
| Headline workload | Direct online streaming; warm service; 100 unique-canary requests; 4K target; 256 requested words; 512-token ceiling; C8 per replica |
| Winner | Two independent TP1 replicas: **1,401.8–1,423.4 aggregate output tok/s**, 100/100 correct canaries |
| Topology result | One TP1: 764.3 tok/s; TP2: 587.9 tok/s and repeatable structured-JSON corruption; DP2: 1,401.8–1,423.4 tok/s |
| Decision | `dp2-throughput-winner`, `tp2-rejected`, `no-promotion` |

**Why it matters:** on this exact two-card PCIe/WSL2 workstation, using each
GPU as an independent TP1 replica produced 2.38–2.42 times TP2's aggregate output
throughput while keeping request-level latency close to one TP1 replica. TP2
also failed a correctness gate, so additional TP2 speed tuning would not make
that measured arm promotable.

**Important caveat:** 1,401.8–1,423.4 tok/s is aggregate output throughput for a
sustained C16 system workload, not single-request decode and not directly
comparable to Helix's 454 tok/s workload. DP2 was addressed directly; no load
balancer, failover policy, router mapping, broad quality suite, or client path
was qualified.

[Evidence manifest](2026-09-04-qwen38-27b-pro6000-possibility-evidence/artifact-manifest.json) ·
[human evidence index](2026-09-04-qwen38-27b-pro6000-possibility-evidence/README.md) ·
[machine summary](2026-09-04-qwen38-27b-pro6000-possibility-evidence/summary.json) ·
[publication summary](2026-09-04-qwen38-27b-pro6000-possibility-evidence/publication-summary.md)

## Exact identities and method

The main SGLang matrix used
`Inferact/Qwen3.8-27B-NVFP4@6128240ebaf4eaa7bad2b3d1c72c37d677c5f462`
and
`incoai/Qwen3.8-27B-DFlash2@dedf8df68adfb1afeaf7b7480c0a0243108177b4`
on
`lmsysorg/sglang@sha256:616a3e97f45191af975896cfa644279096cb31bd408a071c2e99ca7209c3cafe`,
whose image label identifies SGLang source
`5f55db35e926d50676f75b812640ea2410b0fe0e`, CUDA 13.0.3, and
FlashInfer 0.6.17. The RadixArk comparison substituted
`RadixArk/Qwen3.8-27B-NVFP4@319f741cce68d7914884900c138a1fbb70a42f30`.

The alternate-runtime comparison used
`kelnei/Qwen3.8-27B-NVFP4@29099dc7004e5731173af5c5fb5253466aee219c`
on
`vllm/vllm-openai:v0.27.1@sha256:c2f3b1b964e47809b722b5e75b61b1e7b39a50f70388cf2bf2418f16a9f31da2`.
Its two arms differed only by integrated MTP with two speculative tokens.

All headline values come from `capacity-v3` direct streaming artifacts. TTFT
is request start to first non-empty content. Effective prefill divides API
prompt tokens by TTFT, so it includes queueing, scheduling, prefill, and first-
token work. Decode uses API completion tokens after the first token over
client-observed generation time. TPOT and mean ITL are the same per-request
aggregate in this harness; they are not raw token-arrival interval samples.
E2E is request start through stream completion. Percentiles use nearest rank.

## SGLang optimization funnel

The initial target-only arm exposed a configuration lesson: 16 Mamba state
slots admitted only three active requests because this model consumed five
slots per request. Raising the pool to 96 enabled the declared C8 control.
DFlash2 K8 then improved the matched 4K/C8 natural-completion screen from
227.4 to 340.4 aggregate tok/s.

The next rounds changed one main variable at a time. These are 16-request,
natural-completion screening cells, so only within-screen comparisons are
valid. Older screen artifacts retain p50/p95 but not full mean/P99 fields.

| Screen | 4K/C8 aggregate tok/s | Interpretation |
|---|---:|---|
| DFlash2 K4 / K8 / K12 / K16, 2K chunks | 330.9 / 340.4 / **421.7** / 342.8 | K12 was 23.9% above K8; K16 lost the gain |
| K12, chunks 1K / 2K / 8K | **476.2** / 421.7 / 464.0 | 1K was 12.9% above 2K and 2.6% above 8K |
| K12/2K with `torch.compile` | 361.6 | 14.2% below the uncompiled K12/2K arm |
| K12/1K, `extra_buffer_lazy`, 96 slots | 391.4 | slower than the ordinary `extra_buffer` finalist |
| K12/1K, `extra_buffer`, 40 slots | 478.8 | no material short win; its 82K/C8 median E2E was worse than 96 slots |

The retained finalist is therefore K12, 1K chunks, ordinary `extra_buffer`,
and 96 slots. In its 100-request natural-completion validation it completed
100/100 at 420.4 aggregate tok/s with TTFT mean/p50/p95/p99
220.1/182.9/341.9/651.8 ms and E2E 858.3/816.8/1,137.5/1,496.9 ms.

## Matched sustained-output topology result

Each row below uses 100 total requests, unique canaries, 256 requested words,
a 512-token ceiling, and C8 per service. Values are mean / p50 / p95 / p99.

| Topology | Aggregate tok/s | TTFT (ms) | Effective prefill (tok/s) | Decode (tok/s) | TPOT = mean ITL (ms/token) | E2E (ms) |
|---|---:|---:|---:|---:|---:|---:|
| SGLang TP1 | **764.3** | 2415.7 / 2331.3 / 5339.1 / 5513.3 | 2609 / 1515 / 12009 / 12072 | 201.5 / 182.5 / 298.4 / 306.0 | 5.49 / 5.43 / 8.17 / 8.33 | 5223.2 / 4856.4 / 9578.9 / 9758.1 |
| SGLang TP2 | 587.9 | 3361.4 / 3184.4 / 6826.2 / 6890.1 | 1368 / 1149 / 2805 / 2858 | 167.1 / 150.2 / 274.7 / 276.3 | 6.74 / 6.56 / 10.13 / 10.21 | 6805.2 / 6047.0 / 11971.5 / 12072.5 |
| Two SGLang TP1 replicas | **1401.8–1423.4** | 2612.2 / 2407.1 / 5424.0 / 5519.4 | 1760 / 1512 / 3221 / 4262 | 198.0 / 183.1 / 308.9 / 352.4 | 5.57 / 5.44 / 8.17 / 8.22 | 5459.7 / 4865.4 / 9590.3 / 9710.5 |

TP2 was 23.1% slower than one TP1 service and 58.1–58.7% slower than DP2. The two
replicas individually measured 711.7 and 720.0 tok/s; their combined artifact
supports a 1,401.8–1,423.4 tok/s timing bound. DP2 delivered 83.4–86.2%
more aggregate throughput than one TP1 and 138.4–142.1% more than TP2.

**Timing correction during publication review:** the native replica timestamps
retain only whole seconds. The original 1,423.4 point divided combined tokens
by the longer local duration and implicitly assumed aligned starts. A window
consistent with the timestamps and local durations spans 35.9699 to less than
36.5253 seconds, yielding the range above. It is a timing uncertainty bound,
not a statistical confidence interval. The two raw replica artifacts are
unchanged; future coordinated measurements need precise shared-clock bounds.

TP2 also failed strict structured JSON: the response contained a valid object,
a literal `</think>` delimiter, and a duplicate object. The isolated repeat
reproduced the same failure. Smoke, tools 8/8, Responses, and the 100 unique
canaries passed, but one hard correctness failure is enough to reject the arm.

This topology result is host-specific. The two cards have no NVLink, and the
TP2 recipe used conservative WSL2 NCCL controls, disabled custom all-reduce,
and a hash-gated ordinary-NCCL logits-gather fallback. It does not predict
native Linux, NVLink, or a future fixed runtime.

## RadixArk target tradeoff

RadixArk DFlash2 K8 won the natural-completion 4K/C8 target comparison at
499.7 tok/s and produced the best 4K/C1 screen: 111.1 ms median TTFT, 147.1
tok/s median decode, 6.38 ms/token TPOT/ITL, and 394.0 ms E2E. K12/1K was
slower on that target, so K8 remained its retained arm.

On the matched 100-request sustained-output workload, RadixArk K8 measured
746.7 aggregate tok/s. Its median TTFT was 1,239.0 ms, 46.9% lower than the
Inferact finalist, but its median decode was 122.5 versus 182.5 tok/s and its
median E2E was 5,186.3 versus 4,856.4 ms. It is therefore a lower-TTFT
tradeoff, not the sustained-decode or aggregate winner.

## kelnei/vLLM MTP2 matched result

Both vLLM arms passed smoke, JSON, Responses, and shared-prefix tools 20/20.
The runtime counter delta recorded 40,608 accepted of 43,244 drafted tokens,
a 93.9% accepted-draft-token fraction, proving that MTP was active. The matched
100-request sustained-output result was:

| vLLM arm | Aggregate tok/s | TTFT mean / p50 / p95 / p99 (ms) | Effective prefill mean / p50 / p95 / p99 (tok/s) | Decode mean / p50 / p95 / p99 (tok/s) | TPOT/ITL mean / p50 / p95 / p99 (ms) | E2E mean / p50 / p95 / p99 (ms) |
|---|---:|---:|---:|---:|---:|---:|
| no-spec | 315.2 | 1341.9 / 1555.0 / 1977.9 / 3102.2 | 3762 / 2324 / 8450 / 8527 | 45.6 / 46.2 / 49.0 / 54.2 | 21.98 / 21.64 / 23.89 / 23.91 | 12541.9 / 12617.4 / 13035.5 / 13608.3 |
| MTP2 | **503.4** | 1022.6 / 483.3 / 2471.0 / 3424.8 | 5325 / 7595 / 7703 / 7747 | 75.4 / 72.9 / 97.3 / 107.5 | 13.56 / 13.71 / 16.20 / 18.56 | 7922.6 / 7858.4 / 9680.7 / 10750.3 |

MTP2 raised aggregate throughput 59.7%, median decode 57.7%, and reduced
median TPOT/ITL 36.6% and median E2E 37.7% relative to its exact no-spec
control. It remains behind the SGLang finalist's 764.3 tok/s and 4.856-second
median E2E on this workload.

## Context and concurrency boundary

At 32K/C1, median E2E was 4.869 seconds for the Inferact finalist, 4.894 for
TP2, 3.822 for RadixArk K8, and 4.730 for kelnei/vLLM MTP2. The 82K/C8 unique-
prefix stress completed without request loss, but median E2E was 121.4 seconds
for Inferact TP1, 137.6 for Inferact TP2, 93.7 for RadixArk K8, and 97.5 for
kelnei/vLLM MTP2. These are capacity observations, not acceptable interactive
latency. Earlier C1 evidence through 241,153 actual prompt tokens remains in
the bundle; one-sample 128K/250K rows are not stable distributions.

## Correctness, failures, and limitations

- All retained 100-request headline artifacts completed 100/100 with unique
  request canaries. This checks response-marker isolation, not broad quality.
- TP2 strict JSON failed twice with duplicated output and a leaked delimiter;
  that arm is rejected despite otherwise successful capacity runs.
- Natural-completion screens and forced-output headline runs have different
  completion distributions. Their aggregate throughput values are not
  interchangeable.
- DP2 was direct-to-replica measurement. Load balancing, health-aware routing,
  failover, admission coordination, and client behavior remain unqualified.
- The campaign did not run repeated intelligence, agentic, SWE, multimodal,
  endurance, routed, or real-client suites for these profiles.
- Power, clocks, temperature, host-memory pressure, cold startup, compile
  duration, and energy per token were not retained consistently across arms.
- DFlash2 deployment-use and license review remains separate from performance.
- The graph pack is derivative. Raw JSON artifacts and their hashes are the
  authority for every plotted point.

## Restoration and decision

The original dual-GPU GLM service, exact served identity, exclusive-mode
ownership, authenticated route, both GPU residencies, and zero shared-memory
files were restored and verified. No model cache or Docker volume was removed.
During final output validation, managed mode entry was blocked before mutation
by two unrelated adjacent promotion records whose expected output directories
were absent. Only those two expected empty directories were created, the exact
mode-entry validation then passed, and the tooling dependency was recorded for
durable repair. No unrelated evidence was copied or changed. No route, client
catalog, or model was promoted.

The bounded recommendation is:

1. Use two independent SGLang Inferact K12/chunk1K TP1 replicas when aggregate
   throughput is the objective and a separately qualified balancing layer
   exists.
2. Use one SGLang Inferact K12/chunk1K TP1 service for sustained decode/E2E on
   one card; retain RadixArk K8 as the lower-TTFT alternative.
3. Reject this SGLang TP2 recipe on this host. Revisit only with a runtime fix
   for structured output and a native-Linux or improved-interconnect control.
4. Retain kelnei/vLLM MTP2 as a verified alternate-runtime gain over no-spec,
   not as the campaign winner.

This is a benchmark decision only. `promotion_authorized` remains `false`.

## Evidence map

The [evidence index](2026-09-04-qwen38-27b-pro6000-possibility-evidence/README.md)
links every raw optimization, topology, checkpoint, runtime, long-context,
functional, restoration, and derivative artifact. The key machine-readable
claims are in
[`summary.json`](2026-09-04-qwen38-27b-pro6000-possibility-evidence/summary.json),
with exact setup in
[`configuration.json`](2026-09-04-qwen38-27b-pro6000-possibility-evidence/configuration.json)
and metric semantics in
[`workload-manifest.json`](2026-09-04-qwen38-27b-pro6000-possibility-evidence/workload-manifest.json).

## Sources

- [Helix RTX PRO 6000 tuning report](https://helix.ml/blog/chasing-454-toks-qwen38-rtx-pro-6000)
- [Official Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B)
- [Official SGLang Qwen3.8 cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B)
- [Official vLLM Qwen3.8 recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B)
- [RadixArk NVFP4 revision](https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4/tree/319f741cce68d7914884900c138a1fbb70a42f30)
- [kelnei NVFP4 revision](https://huggingface.co/kelnei/Qwen3.8-27B-NVFP4/tree/29099dc7004e5731173af5c5fb5253466aee219c)
- [SGLang DFlash2 concurrency report](https://github.com/sgl-project/sglang/issues/36548)
