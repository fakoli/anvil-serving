# Qwen3.8 27B quant bakeoff on RTX 5090

**Date:** 2026-09-03

**Decision:** prefer the Gittensor RTX5090 target-only NVFP4/SGLang recipe as
the measured TTFT challenger; retain the Unsloth GGUF incumbent; no promotion

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, Blackwell
`sm_120`

**Topology:** isolated direct managed recipe lane; no router alias, client
catalog, or persistent serving assignment changed

## Outcome

The campaign loaded thirteen fresh Qwen3.8 27B target/speculation arms plus the
starting incumbent and ranked them by TTFT, decode, usable context/KV, then
concurrency. Gittensor's target-only NVFP4 checkpoint on SGLang 0.5.18 won the
primary metric with **50.9 ms warm median TTFT** at a 3,613-token prompt. It
also returned the exact marker from a **244,002-token actual prompt**, passed
the C2 workload at 128.7 aggregate output tok/s, and passed repeated bounded
coding, triage, tool, 8K, and 32K context checks.

The result is a challenger, not a promotion. Gittensor's matched advertised
DSpark arm failed during CUDA-graph capture because the target and drafter
matrix shapes were incompatible. SGLang also reported absent calibrated FP8
KV scales and used 1.0 defaults. The exact target-only path is fast and
functional, but broader quality equivalence is not yet demonstrated.

CometKim NInfer MTP3 delivered the highest warm decode rate, 228.0 tok/s, and
passed the 244,002-token actual-prompt request. It failed the general-purpose
hard gate: all three strict tool attempts selected the intended function but
omitted its required `zip` string. The cdiamond iMatrix GGUF MTP8 arm is the
best fresh full-context balanced fallback at 223.1 ms TTFT and 96.0 tok/s.

A final date-aware source refresh found Unsloth's newly published Dynamic V3.0
NVFP4 target plus separate MTP head. The exact revision loaded on the pinned
stock-vLLM runtime. MTP3 passed tools 20/20, measured 388.7 ms warm TTFT,
137.7 tok/s warm decode, 127.5 tok/s at a 53,706-token actual prompt, 83.2%
token acceptance, and 3,198 MiB free. This makes it the strongest clean 64K
speculative arm, not the TTFT or full-context winner.

After all trials, every challenger was unloaded through the managed recipe
surface. The exact starting Unsloth revision and llama.cpp image were restored
healthy, then passed fresh smoke, JSON, and 20/20 shared-prefix tools.

## Result card

| Field | Measured result |
|---|---|
| Campaign | fourteen measured arms across SGLang, llama.cpp, NInfer, and vLLM |
| Rank order | TTFT, decode, usable context/KV, concurrency |
| TTFT winner | Gittensor target-only NVFP4/SGLang: 50.9 ms p50, 277.0 ms p95, 79.5 tok/s decode |
| Decode winner | CometKim NInfer MTP3: 326.3 ms TTFT, 228.0 tok/s decode; strict tools failed 0/3 |
| Full-context balanced fallback | cdiamond iMatrix GGUF MTP8: 223.1 ms TTFT, 96.0 tok/s decode |
| Full-context proof | Gittensor, cdiamond, and CometKim arms returned exact markers at 244,002 API-reported prompt tokens |
| 64K leader | Unsloth Dynamic V3 MTP3: 388.7 ms TTFT, 137.7 tok/s short, 127.5 tok/s at the 53,706-token prompt, tools 20/20 |
| Idle baseline | no GPU processes; 0 MiB reported used; 32,187 MiB free; P8; 0% GPU utilization |
| Reserve | zero added policy reserve for this dedicated campaign only; 420 MiB physical difference remained unavailable |
| Restoration | exact Unsloth GGUF incumbent healthy; smoke/JSON/tools 20/20 pass |
| Decision | Gittensor direct TTFT challenger; incumbent retained; no promotion |

## Exact candidates

| Family | Revision | Runtime | Tested profile |
|---|---|---|---|
| Gittensor RTX5090 NVFP4 | `b8ca3826548c9a7735642feb05c3c473f1fede1f` | SGLang 0.5.18, digest `bde16a84` | target-only 262K and advertised DSpark 165K startup |
| cdiamond iMatrix NVFP4 GGUF | `ac343e8f44caef0896f79d372ecc07ef7ab34ec8` | llama.cpp b10548, digest `cf2e30bc` | 262K no spec / embedded MTP8 |
| QUASAR QAT NVFP4 | `d8e6fbfa3e3a78899b440222b827430045a05b44` | vLLM 0.27.1, digest `c2f3b1b9` | 64K no spec / MTP2 |
| CometKim NVFP4Full NInfer | `4f302e0c324771bbd48c419a8d0319e39334ba23` | `cometkim/ninfer@1455676b` on pinned CUDA base | 262K no spec / MTP3 |
| Red Hat NVFP4 | `285eba88b22cc7664d2e120eca75ddb7c7dfd6b7` | vLLM 0.27.1, digest `c2f3b1b9` | bounded 64K no spec / MTP4 |
| Telperion AutoRound | `4e81b8843cac2a7f053eda6dfd56d11be3dbafe7` | vLLM 0.27.1, digest `c2f3b1b9` | bounded 64K no spec / MTP2 |
| Unsloth Dynamic V3 NVFP4 | `57926baca9a82b4d6906b43f2750d55315f5b10f` | vLLM 0.27.1, digest `c2f3b1b9` | bounded 64K no spec / MTP3 |
| restored incumbent | `unsloth/Qwen3.8-27B-GGUF@4ca72078` | llama.cpp b10548, digest `cf2e30bc` | dynamic Q4_K_XL, native MTP3, 262K |

All candidate configurations are reusable managed serve-recipe registries in
`configs/`; none is a router-selection rule.

The date-aware search did not find an NVIDIA-authored Qwen3.8 27B checkpoint.
Several current third-party checkpoints use NVIDIA Model Optimizer NVFP4, but
that does not make them NVIDIA model releases. The newly material publisher
release was Unsloth Dynamic V3.0, which is included in the local results above.

## Matched performance

The [dedicated comparison page](../benchmarks/qwen38-27b-rtx5090-quant-comparison.md)
contains every fresh warm C1 row, C2 aggregate throughput, long-context proof,
and matched speculation delta. The most decision-relevant rows are:

| Arm | Warm TTFT p50 | Warm decode p50 | Long-context decode | Decision |
|---|---:|---:|---:|---|
| Gittensor target-only | **50.9 ms** | 79.5 tok/s | 57.4 tok/s at 244,002 prompt tokens | TTFT winner |
| cdiamond MTP8 | 223.1 ms | 96.0 tok/s | 41.0 tok/s at 244,002 | balanced full-context fallback |
| QUASAR MTP2 | 267.4 ms | 116.0 tok/s | 37.5 tok/s at 53,706 | long-context regression |
| CometKim MTP3 | 326.3 ms | **228.0 tok/s** | 126.9 tok/s at 244,002 | decode winner, tool gate failed |
| Unsloth Dynamic V3 MTP3 | 388.7 ms | 137.7 tok/s | **127.5 tok/s** at 53,706 | strongest clean 64K MTP arm |
| Red Hat MTP4 | 390.6 ms | 118.9 tok/s | 119.3 tok/s at 53,456 | strong 64K long decode |

The first Gittensor warm sample took 277.0 ms; its p50 lead is real within
this run but more variable than the median alone suggests. Sample counts are
small and p95 is descriptive.

## Functional, capacity, and quality gates

- Every arm that started passed smoke, structured JSON, and the retained
  tool preflight before its performance artifacts were accepted.
- Every timed arm returned the exact marker from its retained context fixture.
  Full-context arms reported 244,002 actual prompt tokens; bounded vLLM arms
  reported 53,456 or 53,706.
- Gittensor passed coding 3/3, timeout triage 3/3, tools 3/3, and the 8K/32K
  context checks. The 32K check measured 3.417 s TTFT and 75.7 tok/s decode.
- CometKim passed coding and triage 3/3 and 8K/32K context checks, but strict
  tools failed 0/3 because the required argument was missing.
- Unsloth Dynamic V3 passed smoke, JSON, tools 20/20, C2, and its 53,706-token
  prompt on both the no-spec and MTP3 arms.
- QUASAR, Telperion, Red Hat, cdiamond, and CometKim completed C2 workloads.
  QUASAR also completed C4. One-active-sequence recipes may queue rather than
  perform simultaneous decode, so accepted request concurrency and active
  decode concurrency are not conflated.

## Feasibility and reserve decision

The user asked that application residency be measured before reducing the
reserve. The incumbent was unloaded, then the idle capture found no compute or
graphics process and no managed serve owner on the card. `nvidia-smi` reported
0 MiB used, 32,187 MiB free of 32,607 MiB, P8, 0% GPU utilization, and 1%
memory utilization.

Only after that capture was the extra policy reserve set to zero for this
dedicated campaign. The 420 MiB total/free difference remained excluded. This
does not change the normal reserve for co-resident operation. No retained arm
experienced OOM, CUDA error, crash, restart, or unexplained request loss.

## Failures that changed the decision

1. **Gittensor DSpark compatibility:** the exact pinned target and drafter
   allocated their KV pools, then CUDA-graph capture failed with target/draft
   hidden-width matrix shapes that cannot multiply. This was not a memory
   failure, so a smaller-context retry would not address the root cause.
2. **CometKim tool schema:** the fastest-decode candidate failed the strict
   general-purpose tool gate 0/3. A parser or template change would be a new
   configuration and requires a complete rerun.
3. **QUASAR deep-context speculation:** MTP2 improved short decode from 75.6
   to 116.0 tok/s but reduced decode at the retained long prompt from 66.6 to
   37.5 tok/s.
4. **Gittensor FP8 KV scales:** the target-only winner used default 1.0 scales.
   Bounded checks passed, but broad fidelity remains an open gate.

## Decision and next gate

- Retain Gittensor target-only NVFP4/SGLang as the priority direct **TTFT
  challenger** on the RTX 5090.
- Keep cdiamond MTP8 as the balanced full-context alternative.
- Keep Unsloth Dynamic V3 MTP3 as the preferred clean 64K speculative
  alternative on this pinned vLLM runtime.
- Keep CometKim MTP3 as a decode research lead only, not a general-purpose
  candidate, until strict tools pass.
- Keep the exact Unsloth GGUF incumbent running. It retains broader prior
  image/OCR, agentic, endurance, routed-client, and deeper context evidence.
- Do not promote. First validate calibrated FP8 KV behavior, run broader
  agentic/SWE and routed-client gates, and obtain a compatible speculative
  drafter if decode acceleration is still desired.

## Evidence

- [Evidence bundle and role ledger](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/README.md)
- [Machine-readable decision summary](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/summary.json)
- [Idle GPU baseline](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/gpu-idle-baseline.json)
- [Gittensor warm timing](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/gittensor-nospec-timing-4k-c1-warm.json)
- [Gittensor long-context result](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/gittensor-nospec-capacity-252928-c1.json)
- [Gittensor quality result](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/gittensor-nospec-quality.json)
- [DSpark startup failure](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/gittensor-dspark-load-failure.json)
- [CometKim MTP3 quality result](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/cometkim-mtp3-quality.json)
- [Restoration proof](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/restoration.json)
- [Pinned source registry](2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/source-registry.json)
