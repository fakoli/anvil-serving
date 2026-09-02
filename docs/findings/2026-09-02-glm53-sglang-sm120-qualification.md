# GLM-5.3-Flash SGLang SM120 qualification

- **Date:** 2026-09-02
- **Decision:** locally `verified` challenger at 245,760 tokens and C1;
  `no-promotion`
- **Measured hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive
  TP=2 over PCIe without NVLink, Windows 11/Docker Desktop/WSL2
- **Evidence:** [sanitized artifact bundle](2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md)

> **Post-qualification update:** After this point-in-time no-promotion
> decision, the operator waived the standing VRAM reserve for the model-only
> GPU pair and authorized the already-tested 393,216-token/C1 profile. The
> managed fix-forward promotion, routed gates, real-client acceptance, and
> exact rollback contract are recorded in the separate
> [393K promotion finding](2026-09-02-glm53-sglang-sm120-393k-promotion.md).

## Result

The community SGLang SM120 recipe is reproducible and useful on this system,
but not with its maximum-context/high-concurrency envelope copied verbatim.
The locally qualified profile uses one running request, adaptive EAGLE MTP
`[3,5]`, and 245,760 configured tokens. It passed the full text/tool/API,
thinking-control, coding-agent, image/OCR, capacity, and endurance gates while
retaining 3,487 MiB free on each card after the complete workload.

The 499,712-token/C4 profile and its C1 reduction are policy-infeasible on this
WSL2 host. A 393,216-token/C1 fix-forward passed every functional and workload
test, but fell from 3,351 MiB idle reserve to 2,101 MiB per card after lazy
media/kernel allocations. Reducing the token pool to 245,760 was the first
profile to preserve the required 3,072 MiB post-workload reserve.

No route was changed and no promotion was authorized. At campaign close, the
exact starting incumbent container, image, model identity, 524,288-token
context, `127.0.0.1:8001` binding, exclusive owner, router readiness, and empty
shared-memory state were restored.

## Immutable candidate

- Model:
  `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- Model cache verification: 178,061,085,084 bytes, no incomplete or broken
  snapshot entries
- Runtime:
  `ghcr.io/ormandj/sglang-glm53-flash-sm120@sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- Runtime tag/source: `v0.1.1-rc.14` /
  `a547c90c74f1363920287eb80adc88a16d1e7005`
- Runtime-reported SGLang source:
  `4c2c169b53dbf362f0cd95111f4ae275cd0167c1`
- Quantization: ModelOpt mixed W4A16 NVFP4 K32 routed experts, FP8
  weight-only attention/shared experts, FP8 E4M3 KV, BF16 recurrent state
- Selected served identity:
  `glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp`
- Selected recipe:
  [`glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml)

The model and runtime are community artifacts. The model repository declares
MIT; downstream users must independently review the checkpoint, runtime, and
their dependencies for their use case.

## Source and claim boundary

The [upstream stable release](https://github.com/ormandj/sglang-glm53-flash-sm120/releases/tag/v0.1.0)
reports native-Linux performance on the same GPU product class and two-card
PCIe topology. Those results, including its C1/C2/C4 throughput and roughly
8,100 tok/s prefill report, are `external-prior` evidence only. The tested
`v0.1.1-rc.14` image contains later correctness, warmup, image-memory, and KDA
changes and was explicitly not upstream-qualified when observed.

The [official SGLang GLM guide](https://docs.sglang.ai/basic_usage/glm.html),
[current GLM-5.3 tracker](https://github.com/sgl-project/sglang/issues/37524),
and [NVIDIA CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)
were used to expand the local correctness and WSL2 compatibility gates. Exact
URLs, dates, evidence types, and decision impact are retained in the
[source registry](2026-09-02-glm53-sglang-sm120-qualification-evidence/source-registry.json).

## Local translation and fixes

The final recipe keeps TP=2, DSA attention, FlashInfer sparse MLA, Triton
linear attention, 4,096-token chunked prefill, FP8 KV, multimodal support, and
adaptive EAGLE MTP. It deliberately omits DCP, expert parallel, HiCache, and
CPU KV offload.

Four runtime compatibility defects were fixed forward:

1. NCCL 2.30.7 failed `ncclCuMemMapAndSetAccess` with CUDA 999. An exact
   two-rank probe passed with only `NCCL_CUMEM_ENABLE=0`.
2. `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` reproduced the first
   distributed `torch.ones` failure. The same probe passed with
   `expandable_segments:False`.
3. SGLang's Torch symmetric-memory logits gatherer raised SIGFPE during graph
   capture. A source-hash- and anchor-gated patch disables only that gatherer
   and selects its ordinary NCCL fallback.
4. The checkpoint template ignored `enable_thinking=false`, leaking reasoning
   into visible content. A source-hash-gated derived template defaults thinking
   on but emits an empty thinking block when disabled, allowing the runtime
   parser to keep visible output clean.

The upstream launcher's image-token cap was also added explicitly as
`--mm-process-config {"image":{"max_image_tokens":3072}}`. The complete
failure chain and exact workaround scope are in the
[friction log](2026-09-02-glm53-sglang-sm120-qualification-evidence/friction-log.md).

## Functional and quality gates

| Gate | Result | Evidence |
|---|---|---|
| Full thinking-disabled preflight | all 10 checks pass; tools 20/20; 200K nominal needle; 194,227-token long tool call; JSON, streaming, tool-result continuation, Responses, image, and OCR pass | [artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-preflight-all-low.json) |
| Thinking enabled | smoke, JSON, tools 5/5, tool-result continuation, and Responses pass with a dedicated reasoning channel | [artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-preflight-thinking-enabled.json) |
| Thinking disabled | the same protocol class returns zero reasoning content and clean visible output | [control](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-thinking-control.json) |
| Coding-agent suite | five deterministic checks × three repetitions = 15/15 at a 2,048-token visible-answer budget | [artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-quality-coding-agent-v2-disabled-r3-visible2048.json) |
| Image/OCR corpus | six cases × two repetitions = 12/12, including two-image comparison | [artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-multimodal-image-c1.json) |
| Endurance | 60/60 C1 requests, no crash or unexplained loss | [artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe240k-endurance-c1-4k-r60.json) |

The initial 393K coding run's Windows move-plan response ended at a 768-token
visible cap. Its validators did not identify an incorrect answer. The unchanged
suite passed 15/15 at 2,048 visible tokens, so the first result is retained as
`visible_answer_budget_exhausted`, not a model-quality failure.

## Performance

### Matched adaptive-MTP control at 131K

| Nominal context | No speculation decode p50 | Adaptive decode p50 | Delta | No-spec prefill p50 | Adaptive prefill p50 |
|---:|---:|---:|---:|---:|---:|
| 4K | 74.10 tok/s | **103.52 tok/s** | **+39.7%** | 12,923 tok/s | 16,728 tok/s |
| 120K | 73.56 tok/s | **95.06 tok/s** | **+29.2%** | 5,926 tok/s | 5,759 tok/s |

The long-context prefill change is -2.8%, inside the predeclared 3% protected
boundary. Functional behavior remained clean on both arms, so adaptive MTP is
the selected decode path. These results do not generalize to a different
checkpoint, SGLang build, GPU, or context envelope.

### Selected 245,760-token/C1 profile

| Nominal context | Measured prompt p50 | TTFT p50 | Effective prefill p50 | Decode p50 | Completed |
|---:|---:|---:|---:|---:|---:|
| 4K | 2,969 | 0.179 s | 16,545 tok/s | **108.57 tok/s** | 3/3 |
| 120K | 96,278 | 16.707 s | 5,763 tok/s | **93.35 tok/s** | 3/3 |
| 230K | 189,627 | 33.815 s | 5,608 tok/s | **95.00 tok/s** | 3/3 |
| 4K endurance | 2,969 | 0.175 s | 16,969 tok/s | **105.82 tok/s** | 60/60 |

The context harness's repeated text does not tokenize one-for-one with its
nominal target; measured prompt counts are reported rather than treating 230K
as an actual-token claim.

### Maximum-envelope evidence

| Profile | Short result | Long result | Reserve result | Decision |
|---|---|---|---|---|
| 499,712 tokens, C4 | 12/12; 147.54 aggregate output tok/s at 4K | four nominal-120K requests passed, but median TTFT was 148.86 s and aggregate output was 0.61 tok/s | 49 MiB/card after startup | `policy-infeasible` |
| 499,712 tokens, C1 | functional pass | not advanced to full workload | 2,347 MiB/card after startup | `policy-infeasible` |
| 393,216 tokens, C1 | 112.07 tok/s at 4K; 60/60 endurance at 102.19 tok/s | 304,491-token measured prompt p50 at 99.79 decode tok/s; all functional/quality/image gates pass | 3,351 MiB idle, **2,101 MiB after workload** | `policy-infeasible` |
| 245,760 tokens, C1 | 108.57 tok/s at 4K; 60/60 endurance at 105.82 tok/s | 189,627-token measured prompt p50 at 95.00 decode tok/s | 4,733 MiB idle, **3,487 MiB after workload** | `verified` challenger |

The external stable release's much higher aggregate decode results are not
reproduced here. Differences include the runtime revision, WSL2 versus native
Linux, context envelope, and locally required NCCL/allocator/symmetric-memory
fallbacks.

## Kernel and log review

The SM120 sparse-MLA `glm53_nope` CPB calibration rejected implausible fitted
constants on both ranks and used its C++ heuristic. The final endpoint remained
healthy and passed all end-to-end gates. Optional NCCL plugin-load notices and
Triton/Torch compiler deprecations were non-fatal; the bounded final log audit
found no traceback, restart, CUDA error, or OOM.

No kernel-tune artifact was adopted. A missing or rejected calibration is an
optimization lead, not evidence of improvement. Any tune must stay pinned to
this exact model geometry, image, SGLang/Triton/CUDA build, TP size, dtype, and
GPU product and must beat the default in a matched end-to-end A/B.

## Restoration

The candidate was unloaded through `models recipes unload`. The first restore
attempt was refused before mutation because the chosen split restore group did
not contain the manifest's declared rollback identity. A subsequent attempt
reached incumbent health but failed router readmission with HTTP 401; the
transaction preserved and stopped the retained incumbent as designed. Loading
only the configured router token from the user-local credential file into the
process allowed the managed transaction to complete.

The final snapshot matched the starting state:

- retained container ID
  `45a009b848afcbb8c6b979178f2b493c6f15327a964e93190affcd60811b2ac0`;
- incumbent image
  `sha256:4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939`;
- exact model
  `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-524k-vision-xgfix` at 524,288
  tokens and `127.0.0.1:8001`;
- `dual-gpu-exclusive` owner
  `tp2-glm53-k3-dflash2-k5-524k-xgrammar` on both GPU roles;
- router container running and `primary-local` ready with the exact incumbent
  identity;
- zero shared-memory/offload files.

A fresh goal-closure recheck at `2026-09-02T12:15:45Z` repeated those managed
state assertions, confirmed the retained container ID, passed one authenticated
`llm.primary` routed smoke, found both GPUs, and again found no candidate
container or shared-memory residue.

See the machine-readable [restoration record](2026-09-02-glm53-sglang-sm120-qualification-evidence/restoration.json).

## Decision

Retain the 245,760-token/C1 adaptive-MTP recipe as a locally verified
challenger and reproducible fallback/experiment. Do not promote it from this
campaign. The current EXL3 K3 plus DFlash2 K5 524K text/image/OCR profile
remains the published reference.

Future work is bounded to two independent questions: whether a pinned CPB tune
beats the qualified heuristic path end-to-end, and whether a revised runtime
can qualify C2+ at long context while preserving the 3 GiB post-workload
reserve. Neither is implied by this result.
