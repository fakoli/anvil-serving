# DeepSeek V4 Flash 0731 r16 DSpark qualification

**Capture window:** 2026-08-01 to 2026-08-02 UTC<br>
**Decision:** priority intelligence `challenger`, `no-promotion`<br>
**Measured hardware:** 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive
TP=2 over PCIe without NVLink<br>
**Evidence:** `functional`, `capacity`, and bounded `quality`

## Outcome

The official `deepseek-ai/DeepSeek-V4-Flash-0731` checkpoint now has a pinned,
reproducible 131,072-token vLLM recipe on the dual-PRO workstation. The final
profile passed low, high, and max reasoning preflights; completed 32K, 64K, and
128K correctness probes; passed all 27 repeated coding, intelligence, session,
and tool attempts; and proved that DSpark K5 materially improves the matched
single-user workload.

In the same-image A/B, DSpark increased median per-request decode from 64.9 to
130.7 tok/s, increased aggregate output from 59.6 to 101.7 tok/s, and reduced
median end-to-end latency from 3.88 to 1.60 seconds. The tradeoff is memory:
both DSpark and its no-spec control failed the standing 3 GiB reported-free
VRAM policy on both cards. A successful 128K request is therefore functional
and capacity evidence, not promotion evidence. No production alias or normal
split-mode service changed.

## Immutable identity and translated recipe

| Component | Pinned value |
|---|---|
| Model | `deepseek-ai/DeepSeek-V4-Flash-0731` |
| Model revision | `9e165c30e2704aec5d9d593cce3eebd58bbef1cb` |
| Image | `voipmonitor/vllm@sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f` |
| vLLM base commit | `30038602b71395f481ef4a6edfe4fcf8551d9c15` |
| vLLM result tree | `1e9c9c3475fa30ab48d5639f8882f1e93bb552bf` |
| FlashInfer commit | `801d57a08958c13d375ddbb6be3be4808f48a708` |
| Quantization | official mixed FP8/FP4 checkpoint; B12X W4A8 NVFP4 MoE and FP8 dense GEMM |
| KV | FP8 MLA KV |
| Parallelism | TP=2, DCP=1, exclusive ownership |
| Context and admission | 131,072 tokens, `max_num_seqs=8`, 8,192 batched-token cap |
| Speculation | DSpark fixed depth, five draft tokens, probabilistic draft sampling |
| Loader | InstantTensor buffered |

The exact managed candidate is
[`configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml).
The causal control is
[`configs/deepseek-v4-flash-0731-r16-b12x-nospec-128k-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-nospec-128k-recipe.toml).
Both use named model, JIT, and temporary-data volumes, so the image and compiled
cache can be reused without baking host paths into the recipe.

The source recipe targeted native Linux and 600 W cards. The working WSL2
translation disables direct NCCL P2P and NCCL cuMem device/host allocation,
retains shared-memory transport, and disables PyTorch expandable segments.
These are platform adaptations already required by this workstation, not
model-performance tuning.

### What the external "tweak" actually is

The July 31 X self-thread is an eight-post deployment record, not a missing
one-line setting. It reports that stock vLLM routes DSpark's five-token verify
through the SM120 sparse-MLA prefill path and asserts because that path requires
more than 64 tokens. The r16 image adds `B12X_MLA_SPARSE`, an SM120 backend that
handles the multi-token verify path. It also drops the B200-only
`deep_gemm_mega_moe` and `use_fp4_indexer_cache` options from DeepSeek's
eight-B200 command.

The thread's working defaults match the pinned local recipe: B12X A8, TP=2,
DSpark K5, 131,072 tokens, 0.975 memory utilization, and InstantTensor. Its
author explains `next_n=6` as five draft tokens plus one verification token and
warns that FP8 repacks which remove `expert_dtype` can make the loader allocate
the wrong expert tensors. The reported K5 result beat K7 in that external
harness. These are firsthand implementation leads; the local functional and
paired A/B artifacts remain the qualification evidence.

## Functional and bounded quality gates

| Gate | Result |
|---|---|
| DSpark low reasoning | smoke, JSON, three typed tools, streaming tools, tool-result continuation, and Responses passed |
| DSpark high reasoning | smoke, JSON, tools, and tool-result continuation passed |
| DSpark max reasoning | smoke, JSON, tools, and tool-result continuation passed |
| Same-image no-spec low control | same full low-reasoning gate passed |
| Coding and intelligence | 9 items, three attempts each, **27/27 passed** |

The 27-attempt suite covered unified-diff editing, timeout triage, multi-turn
recall, typed tool calls, a Python timeout-guard patch, mutable-default root
cause, a safe Windows recursive-move plan, the exclusive-TP=2 invariant, and
ticket-before-workaround behavior. It used 512 visible-answer tokens plus
4,096 reasoning-headroom tokens and retained full visible answers, finish
reasons, reasoning metadata, and deterministic checks. This is a bounded local
agent slice, not a claim of general benchmark superiority.

Raw evidence:

- [DSpark low preflight](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/preflight-r16-b12x-dspark5-128k-low.json)
- [DSpark high preflight](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/preflight-r16-b12x-dspark5-128k-high.json)
- [DSpark max preflight](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/preflight-r16-b12x-dspark5-128k-max.json)
- [No-spec low preflight](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/preflight-r16-b12x-nospec-128k-low.json)
- [Repeated coding/intelligence result](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/quality-r16-b12x-dspark5-coding-intelligence-low.json)

## Context timing and memory

The first complete ladder was colder and retained no prompt-prefix advantage.
The later hardware-sampled ladder was warmed. Both passed, so they are
published separately rather than blended into one percentile.

| Target | Actual prompt | Cold TTFO / visible TTFT | Cold effective prefill | Cold decode | Warm TTFO / visible TTFT | Warm effective prefill | Warm decode |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 32K | 23,831 | 15.14 / 19.30 s | 1,574 tok/s | 102.1 tok/s | 3.60 / 6.89 s | 6,616 tok/s | 118.7 tok/s |
| 64K | 67,415 | 38.60 / 41.63 s | 1,747 tok/s | 89.0 tok/s | 9.70 / 12.41 s | 6,953 tok/s | 114.0 tok/s |
| 128K | 125,785 | 84.75 / 85.75 s | 1,484 tok/s | 113.4 tok/s | 19.44 / 23.81 s | 6,469 tok/s | 128.9 tok/s |

`effective_prefill_tok_s` is client-observed prompt tokens divided by TTFO; it
includes queueing, scheduling, prompt processing, and first output. It is not a
standalone kernel-prefill measurement. The 128K target was clamped to 126,464
planned tokens to leave the declared completion margin inside the 131,072-token
serve.

The 250 ms hardware sampler observed nearly flat active-request VRAM because
the engine reserves KV capacity at startup. The longer prompts primarily
increased how long both GPUs remained at high utilization and power.

| Context | `dark-compute-a` minimum free / peak power | `dark-compute-b` minimum free / peak power | 3 GiB reserve |
|---:|---:|---:|---|
| 32K | 1,179 MiB / 240.85 W | 2,031 MiB / 228.31 W | fail on both |
| 64K | 1,197 MiB / 250.26 W | 2,031 MiB / 237.94 W | fail on both |
| 128K | 1,203 MiB / 265.32 W | 2,031 MiB / 251.88 W | fail on both |

Raw evidence:

- [Cold context ladder](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/quality-r16-b12x-dspark5-context-ladder-low-r2.json)
- [Warmed context ladder](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/quality-r16-b12x-dspark5-context-ladder-low-r3-telemetry.json)
- [Per-context GPU sampling](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/hardware-r16-b12x-dspark5-context-sampling.json)

## DSpark versus no-spec A/B

The paired variable was fixed-depth DSpark with five draft tokens versus
`MODE=dspark-mtp0`. Image, checkpoint, B12X kernels, FP8 KV, TP=2 transport,
allocator, context, `max_num_seqs`, memory ceiling, prompts, output budget, and
timing boundaries were held constant. Each published lane uses the median of
three successful run-level p50 values; one additional no-spec run with a
reasoning-only completion is retained but excluded from that aggregation.

| Metric | DSpark K5 | No spec | DSpark delta |
|---|---:|---:|---:|
| Per-request decode p50 | 130.7 tok/s | 64.9 tok/s | **+101.4%** |
| Aggregate output | 101.7 tok/s | 59.6 tok/s | **+70.5%** |
| TTFO p50 | 334 ms | 315 ms | 5.9% slower |
| First-visible TTFT p50 | 1.43 s | 3.53 s | 59.6% lower |
| Generation p50 | 1.27 s | 3.59 s | 64.7% lower |
| E2E p50 | 1.60 s | 3.88 s | **58.8% lower** |

DSpark exposed 138,459 KV-cache tokens in the compared start; the no-spec
control exposed 257,515. Post-run DSpark used 1,587 and 2,328 MiB more VRAM
than the control on the two cards. Cumulative counters across the DSpark serve
recorded 4,865 accepted of 8,830 drafted tokens: 55.1% acceptance, or 2.75
accepted tokens per draft. Because those counters cover functional, capacity,
and context requests, they describe the serve window rather than one isolated
prompt. The thread reports 56.7% acceptance, close to the local 55.1%, which
supports behavioral parity without making its unpublished timer comparable.

Raw evidence:

- [Paired comparison](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/r16-b12x-dspark5-vs-nospec-4k-c1-comparison.json)
- [Cumulative DSpark acceptance](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/dspark-r16-b12x-k5-cumulative-after-context.json)
- [DSpark post-context memory](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/hardware-r16-b12x-dspark5-post-context.json)
- [No-spec post-capacity memory](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/hardware-r16-b12x-nospec-post-capacity.json)

## Failed attempts and durable fixes

1. The first managed start failed before GPU allocation because complete-cache
   offline mode localized the target model but not the nested speculative model
   ID. The recipe now supplies the same exact snapshot through both model-path
   variables.
2. The second start reached TP worker initialization and failed NCCL direct P2P
   under WSL2. The final recipe applies the established shared-memory NCCL and
   allocator translation.
3. The translated engine loaded target and draft weights but `max_num_seqs=16`
   left the 131K KV gate short by 0.15 GiB after CUDA-graph profiling. The c1
   qualification profile reduces admission to eight and the graph cap to 48;
   it does not claim the external 16-sequence profile.
4. One of twelve earlier no-spec/capacity requests and one of four same-image
   control runs exhausted their reasoning allowance without visible content.
   Both failures remain retained evidence.
5. The context ladder initially summarized timing globally but dropped the
   per-context timing fields. The harness now retains prompt/output counts,
   TTFO, TTFT, generation, effective prefill, decode, inter-token timing, and
   reasoning/content chunks for every context row, with independent regression
   coverage.

The managed failure path preserved failed containers for authoritative logs,
and Anvil reclaimed WSL page cache between lanes. No raw-Docker lifecycle path
became the operating procedure.

## Caveats and decision

- The community performance lead used 600 W RTX PRO cards and an unpublished
  client timer. Its reported 230-250 tok/s is not directly comparable to the
  local 300 W Max-Q, client-observed 130.7 tok/s decode result.
- WSL/WDDM reports global allocations that include host display and runtime
  use. This explains some difference from native Linux but does not authorize
  weakening the declared reserve after observing the result.
- The model weights are MIT. The pinned runtime source repository has no root
  license at the tested revision, so this result does not authorize image or
  derived-code redistribution.
- The quality suite is deliberately agent-shaped but small. Broader coding,
  tool recovery, and long-session work remain useful follow-ups.
- The 131K profile does not validate DeepSeek's advertised 1M input window or
  its very large high/max output recommendations. A 256K local serve remains
  unqualified.

Retain the r16 DSpark recipe as the preferred DeepSeek 0731 performance lane
for further experiments. Keep it `no-promotion` until either the 3 GiB physical
reserve is met with a materially revised recipe or the reserve policy is
separately reviewed and changed before a run. Production aliases and the
normal split topology remain unchanged.

## Provenance

The external prior and exact pinned runtime are recorded in the
[source capture](2026-08-01-deepseek-v4-flash-0731-nvfp4-evidence/external-dzeeksa-r16-thread.json).
The complete private attempt log and acceptance contract are retained in
`.tickets/closed/2026-08-01-deepseek-0731-r16-b12x-performance-spike.md`.
The evidence directory totals approximately 0.57 MB; every raw file is below
the 1 MiB per-file publication limit.
