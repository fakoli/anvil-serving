# Local Serving-Stack Blueprint — Tuned to My Claude Code Usage

**Owner:** Sekou Doumbouya - **Date:** 2026-06-26 - **Rig:** fakoli-dark (RTX PRO 6000 96GB + RTX 5090 32GB, sm_120, no NVLink, WSL2/Docker)
**Method:** deep-research fan-out (5 angles) + adversarial verification of the load-bearing claims. Companion to `USAGE-BASELINE-AND-INFERENCE-DESIGN.md` and `../fakoli-local-stack/DUAL-GPU-BLACKWELL-SETUP.md`.
**Scope:** the *local serving stack* only (engine + KV/prefix/concurrency tuning + model + dual-GPU topology). OSS-only, current hardware. Optimizing for capability+context, concurrency/throughput, and latency — not cost.

> **Bottom line:** Your wins come from **prefix caching + KV-cache capacity + a high concurrency ceiling**, not from the exotic features. Run **two pinned single-GPU instances behind a router** (never tensor-parallel across these cards). Serve a **coding-specialist MoE on the 96GB card at FP8 (FP8 KV)**, push context to **128K-256K** (your MoE's KV is cheap), turn the concurrency knobs up, and **skip speculative decoding and P/D disaggregation** — both are anti-patterns for your prefill-bound, tiny-generation profile. NVFP4 is the future on Blackwell but is **not yet safe for large prefills on sm_120** — stay FP8 until you validate.

---

## 1. Decision table (what to do on each lever)

| Lever | Verdict for your profile | Why |
|---|---|---|
| **Engine** | **Stay SGLang** (best prefix reuse) with **vLLM as the proven fallback** | RadixAttention is the ideal structure for "one shared trunk, many short branches." vLLM has the most-proven sm_120 recipe if SGLang correctness wobbles. |
| **Dual-GPU topology** | **Two independent pinned instances + router. NOT tensor-parallel.** | No NVLink + 5090 P2P disabled → TP all-reduce is host-staged and crippled; TP also wastes the 96GB card. Replicas have zero cross-GPU collective traffic. |
| **Quant (weights)** | **FP8 now**; NVFP4 only after a validated sm_120 build | NVFP4 doubles throughput on Blackwell *in theory*, but on sm_120 it has live crash bugs on large prefills (your exact regime). |
| **KV cache** | **FP8 KV (`fp8_e4m3`)**; never NVFP4 KV on sm_120 | FP8 KV is ~free in accuracy on Blackwell (the Hopper accuracy bug doesn't apply to your card); NVFP4 KV has no sm_120 attention kernel and crashes on first request. |
| **Context window** | **Raise from 32K → 128K (cover ~91%) or 256K (cover ~99.5%)** | Role-split: subagent calls sit at median 55K, p95 159K; **128K covers 90.6%** of specialist calls, **256K covers 99.5%** (your 32K covers only 22%). MoE KV is cheap (~48 KB/token FP8), so context isn't the constraint. |
| **Prefix cache** | **On + add host-RAM overflow (SGLang HiCache / vLLM LMCache)** | 86% of your work is forked subagents sharing a harness prefix; keep the trunk resident so fan-out doesn't evict it. |
| **Concurrency** | **Turn it up hard** (see §4) | Bursts of 20-160 agents; your current "3 concurrent" queues instantly. |
| **Speculative decoding** | **Don't.** (If ever: EAGLE-3/MTP on-GPU, never ngram) | Helps only decode; you're prefill-bound with 56-token median gens; regresses TTFT at scale; ngram corrupts your tool calls. |
| **P/D disaggregation** | **Skip** | Helps decode-heavy workloads; yours is the opposite. KV-transfer tax over PCIe with no NVLink makes it net-negative. |
| **5090's job** | **Second replica / fast helper / draft-or-gaming toggle** | Adds concurrency headroom with zero P2P penalty; keeps the gaming toggle intact. |

---

## 2. Engine: SGLang primary, vLLM proven fallback

Both engines now have the two features your profile needs — **automatic prefix caching** and **chunked prefill** — so the decision is about prefix-reuse structure and sm_120 robustness.

- **SGLang (stay here):** RadixAttention is a radix tree over all live + cached requests; your fan-out (one trunk, many short branches) maps onto a shared subtree for free, and **HiCache** spills the trunk to your 96 GB of host RAM so a 20-160 agent wave can't evict it. It's the lowest-friction strong fit. **Caveat:** several open 2026 sm_120 bugs exist, but most are **DeepSeek-MLA / EP=8 specific**, not your Qwen MoE in FP8 — so do a one-time **correctness pre-flight** (below) before trusting it.
- **vLLM (fallback, keep warm):** the most-proven public recipe on your exact card (Qwen3.6 on RTX PRO 6000 96GB under WSL2, FP8, chunked prefill + APC, 256K ctx). If SGLang produces garbage on sm_120 for your model or a release regresses, switch here without re-architecting.
- **Skip TensorRT-LLM** (compile/rebuild tax fights agentic iteration), **LMDeploy** (thin sm_120 evidence), and **NVIDIA Dynamo** (a multi-node orchestration layer — disaggregation overhead > benefit on one box).

**sm_120 correctness pre-flight (do once per engine/model/quant):** load the model, run (a) a long-context needle-in-a-haystack at ~128K, (b) a batch of 20 identical-prefix tool-calling requests, (c) a structured-output/JSON task. Confirm outputs are clean (no garbage tokens, correct tool-call XML) before trusting throughput numbers.

---

## 3. Model + quant for the 96GB specialist serve

**Primary pick: Qwen3-Coder-Next 80B-A3B** — purpose-built coding-agent MoE (~3B active → very high tok/s), 256K native context, and **validated on your exact card**: FP8 holds ~336 tok/s across 8K-262K context; Q8 (~80 GB) fits with ~16 GB left for KV at 256K; **prompt processing stays >1400 tok/s even at 256K** (exactly what a prefill-bound workload needs). This is a meaningful upgrade over your current Qwen3.6-35B-A3B for the *coding-specialist* role.

Alternatives: **GLM-4.6-Air** (heavier ~12B active → smarter but slower; quality-upgrade option) and **gpt-oss-120b (MXFP4)** as a fast second opinion or the 5090's helper. Keep your current **Qwen3.6-35B-A3B** as the known-good baseline.

**Quant: FP8 weights + FP8 KV today.** NVFP4 is the native-speed Blackwell format (~2× BF16) and is the eventual target, but on **sm_120** it currently has live, *directly-relevant* failures: a TurboQuant workspace crash on any prompt **>4096 tokens** (continuation/chunked prefill — i.e. your whole workload), NVFP4 **KV** crashing on first request (no sm_120 FMHA kernel), and NVFP4 MoE GEMM kernels still falling back. So: **run FP8 now; revisit NVFP4 only with a validated FlashInfer-b12x/ModelOpt build and a passing pre-flight**, and even then keep KV at FP8.

---

## 4. Concrete starting config

### Topology: two pinned instances + router (run under WSL2/Docker)
```bash
# PRIMARY — coding specialist on the 96GB RTX PRO 6000 (GPU 1)
CUDA_VISIBLE_DEVICES=1 python -m sglang.launch_server \
  --model <qwen3-coder-next-80b-a3b-fp8> --port 30000 \
  --kv-cache-dtype fp8_e4m3 \
  --context-length 262144 \
  --chunked-prefill-size 4096 \
  --mem-fraction-static 0.90 \
  --max-running-requests 192 \
  --cuda-graph-max-bs 256 \
  --schedule-policy lpm \
  --enable-hierarchical-cache --hicache-ratio 2 --hicache-io-backend kernel \
  --served-model-name coder-specialist
# RadixAttention prefix cache is ON by default.

# SECONDARY — fast helper / overflow replica on the 5090 (GPU 0, toggleable)
CUDA_VISIBLE_DEVICES=0 python -m sglang.launch_server \
  --model <small-fast-coder-fp8> --port 30001 --context-length 65536 \
  --kv-cache-dtype fp8_e4m3 --served-model-name coder-helper

# ROUTER in front (model-name routing, or cache-aware load-balance of same-model replicas)
# sgl-router (RadixAttention-aware) for same-model replicas:
python -m sglang_router.launch_router \
  --worker-urls http://localhost:30000 http://localhost:30001 --policy cache_aware
# or LiteLLM proxy if the two cards run DIFFERENT models (route by model name).
```

### vLLM equivalent (fallback)
```bash
CUDA_VISIBLE_DEVICES=1 vllm serve <model-fp8> --port 8000 \
  --kv-cache-dtype fp8 \
  --max-model-len 262144 \
  --enable-chunked-prefill --enable-prefix-caching \
  --gpu-memory-utilization 0.93 \
  --max-num-seqs 256 --max-num-batched-tokens 16384 \
  --long-prefill-token-threshold 8192
# add LMCache CPU offload if the shared prefix won't stay resident under fan-out.
```

### Tuning intent behind the knobs
- **Concurrency ceiling** = KV-cache pool size. Push `--mem-fraction-static` (SGLang) / `--gpu-memory-utilization` (vLLM) as high as it goes without OOM; that's your single biggest lever for absorbing a 20-160 wave.
- **`--max-running-requests 192`** admits a full p95-to-near-max wave instead of queueing.
- **Chunked prefill** keeps a 578K-token prefill from blocking the 56-token decodes; shrink the chunk if long prefills starve decodes, grow it for faster per-prompt TTFT.
- **`--cuda-graph-max-bs 256`** so CUDA graphs cover the big batch.
- **HiCache** gives the shared harness prefix a host-RAM tier so fan-out can't evict it.
- If SGLang under-admits a wave (you see `token usage < 0.9` with a non-empty queue), **lower `--schedule-conservativeness` toward 0.3** — the classic symptom of short, early-EOS generations.

### Prompt-side levers (free, biggest prefix-cache wins)
Front-load the **stable** harness/system prompt + tool/skill schemas + shared context at the very start; put per-agent variable content at the end; strip per-request timestamps/IDs from the prefix; serialize structured context deterministically (stable key order). A single differing byte before the divergence point breaks the whole cached prefix. Dispatch sibling subagents close in time so the trunk stays resident.

---

## 5. Don't-do list (verified anti-patterns for this profile)
- **No tensor-parallel across the 5090 + RTX PRO 6000** — no NVLink + disabled P2P → host-staged all-reduce; community P2P patches yield only 10-30% and are fragile.
- **No speculative decoding by default** — only accelerates decode; you're prefill-bound with 56-token median gens; EAGLE-3 gains collapse at high batch and can regress TTFT. Independent test: *no* llama.cpp spec-decode mode beat baseline on Qwen3.6-35B-A3B. If you ever try it: EAGLE-3/MTP co-located on the target GPU, **never ngram** — and if ngram, `prompt_lookup_min>=8` or it corrupts ~50% of tool calls. Update (2026-06-23): **dFlash** (NVIDIA/UCSD block-diffusion drafter, SGLang-supported, *quality-preserving* so no tool-call corruption, ~2.3x vs EAGLE-3 on coding) is now the better-than-EAGLE option to **A/B** — but it's still decode-only, so expect limited end-to-end gain on your prefill-bound, short-generation profile; worth it mainly for the long-generation tail. Needs a matching dFlash draft checkpoint for the chosen model.
- **No P/D disaggregation** — decode pool would idle (short outputs) and the large-context KV blob is expensive to move over PCIe.
- **No NVFP4 weights or KV on sm_120 yet** — live crash bugs on >4096-token prefills and on NVFP4 KV first-request.
- **No cross-GPU draft model on the 5090** — not natively supported; per-step PCIe hop kills it for tiny gens.

---

## 6. Benchmark before you trust it
Replay *your* measured distribution, not generic 512-in/256-out:
1. Build a request set matching the baseline: context sizes ~ {p50 65K, p90 362K, p95 578K}, generation ~ {median 56, p95 2,465}, and a **fan-out burst of 20 sharing one prefix** (plus a 160 stress test).
2. Measure: **prefix-cache hit rate** (the #1 KPI for you), **TTFT** p50/p95, queue depth during the burst, KV-cache utilization, and end-to-end latency. (`data/aggregate.json` has the exact percentiles to reproduce.)
3. Compare SGLang-FP8 vs vLLM-FP8 on *your* set; only consider NVFP4 if a build passes the pre-flight and beats FP8 on TTFT without correctness loss.

---

## 7. Caveats / what's still uncertain
- **No public benchmark isolates these engines on consumer/workstation sm_120 for large-context agentic traffic** — most numbers are H100, short-prompt. Treat rankings as directional; your pre-flight + replay is the real test.
- **SGLang sm_120 correctness for your specific MoE in FP8 is unconfirmed** — pre-flight first; vLLM is the proven fallback.
- **Exact KV bytes/token depend on your model's `config.json`** (layers / KV-heads); the "context is cheap" conclusion holds for a Qwen3 MoE but verify live VRAM after load.
- **NVFP4 on sm_120 is moving fast** — re-check the bug status before adopting; it may become safe within a release or two.
- Throughput figures for Qwen3-Coder-Next vary widely by quant/engine (40-124 tok/s llama.cpp Q-levels vs ~336 tok/s FP8 vLLM) — benchmark on your stack.

---

## Sources
**sm_120 / NVFP4 status:** vLLM #43357 (TurboQuant >4096-tok prefill crash) https://github.com/vllm-project/vllm/issues/43357 · vLLM #43562 (nvfp4 KV crash, no sm_120 FMHA) https://github.com/vllm-project/vllm/issues/43562 · vLLM #31085 (native NVFP4 MoE kernels) https://github.com/vllm-project/vllm/issues/31085 · FlashInfer #2577 (mm_fp4 GEMM broken sm_120) https://github.com/flashinfer-ai/flashinfer/issues/2577 · Elevata "NVFP4 on Blackwell SM120 — what worked" https://elevata.io/en/nvfp4-inference-blackwell-sm120-gpus-what-worked · vLLM 25.09 release notes https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-25-09.html · proven recipe: lastloop-ai/vllm-blackwell-guide https://github.com/lastloop-ai/vllm-blackwell-guide
**Prefix cache / KV:** vLLM Automatic Prefix Caching https://docs.vllm.ai/en/stable/design/prefix_caching/ · SGLang HiCache (LMSYS) https://www.lmsys.org/blog/2025-09-10-sglang-hicache/ · vLLM×Mooncake agentic workloads (May 2026) https://vllm.ai/blog/2026-05-06-mooncake-store · vLLM FP8 KV-cache (Apr 2026) https://vllm-project.github.io/2026/04/22/fp8-kvcache.html
**Concurrency / spec-decode / P/D:** SGLang hyperparameter tuning https://docs.sglang.io/advanced_features/hyperparameter_tuning.html · vLLM optimization/tuning https://docs.vllm.ai/en/stable/configuration/optimization/ · Red Hat spec-decoding gpt-oss (Apr 2026) https://developers.redhat.com/articles/2026/04/16/performance-improvements-speculative-decoding-vllm-gpt-oss · vLLM #40875 (ngram tool-call corruption; fix prompt_lookup_min=8) https://github.com/vllm-project/vllm/issues/40875 · "Tested every llama.cpp spec-decode mode on Qwen3.6-35B-A3B — none faster" https://hackmd.io/@thc1006/SJly6IE6Wx · vLLM single-node P/D https://vllm.ai/blog/2026-04-07-moriio-kv-connector
**Topology / model:** vLLM #21491 (TP broken on sm_120) https://github.com/vllm-project/vllm/issues/21491 · NCCL #1637 (dual-5090 P2P) https://github.com/NVIDIA/nccl/issues/1637 · vLLM parallelism docs https://docs.vllm.ai/en/latest/serving/parallelism_scaling/ · SGLang Router https://docs.sglang.io/advanced_features/router.html · Qwen3-Coder-Next on RTX PRO 6000 (Hardware-Corner) https://www.hardware-corner.net/qwen3-coder-next-hardware-requirements/ · RTX PRO 6000 local LLM benchmarks https://www.vaditaslim.com/blog/ai/local-llm-benchmarks-rtx-pro-6000 · Yotta Labs Qwen3.6-35B-A3B on RTX PRO 6000 https://www.yottalabs.ai/post/how-to-run-qwen3-6-35b-a3b-on-a-single-gpu-rtx-pro-6000-guide

### Changelog
- **2026-06-26** — Created from deep-research fan-out (5 angles) + verification of the load-bearing sm_120/NVFP4/spec-decode/model-fit claims. Recommends SGLang-FP8 primary + vLLM fallback, two-instance + router topology, 128-256K context, aggressive concurrency, prefix-cache/HiCache, and no spec-decode/P-D/NVFP4-on-sm_120 yet.
