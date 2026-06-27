# OSS Model Landscape (2026) — Agentic vs Coding, by What Fits Your Rig

**Owner:** Sekou - **Date:** 2026-06-26 - **For:** the local specialist tier (RTX PRO 6000 96GB + RTX 5090 32GB)
Companion to `LOCAL-SERVING-STACK-BLUEPRINT.md`. Models change weekly — treat scores as directional and re-check before committing.

## "Agent" vs "coding" — they're different benchmarks
- **Coding** ability is measured by **SWE-bench Verified / SWE-Bench Pro** (can it actually fix a repo-scale bug). Optimize here for code generation + repo edits.
- **Agentic / tool-use** ability is measured by **BFCL** (single-call function-calling precision) and **tau-bench** (sustained multi-turn tool reliability). These *disagree* by design — BFCL rewards one clean call, tau-bench rewards staying coherent across a long tool loop.
- **Your specialist tier needs the intersection:** it executes bounded coding work-packets *with* tool calls. So you want **coding-strong + reliable tool-use**, at ≤~128-256K context (your role-split: 128K covers 90.6% of subagent calls, 256K covers 99.5%). The "agentic coding" models below are built for exactly that overlap.

**A third category — *world models* (e.g. Qwen-AgentWorld) — is NOT a coder.** Qwen-AgentWorld (35B-A3B, 256K, Apache-2.0; also a 397B-A17B) *simulates* agent environments (predicts the next-state observation for MCP/Terminal/SWE/Web/OS/etc.). It models what an environment would return — it does not write or edit code. Useful for agent training, model-based planning, or cheap environment simulation/eval (AgentWorldBench), **not** as your specialist serve. Fits your rig, wrong job.

---

## Tier 1 — Fits the 96GB RTX PRO 6000 (your primary specialist serve)
All MoE (low active params → high tok/s, long-context friendly). Quant = FP8 today on sm_120 (NVFP4 once validated).

| Model | Params (total/active) | Context | Coding (SWE) | Agentic/tool | Fit @96GB | Notes |
|---|---|---|---|---|---|---|
| **Qwen3-Coder-Next 80B-A3B** ⭐ | 80B / ~3B | 256K (→1M YaRN) | ~70% SWE-bench Verified | Strong (built for agents) | FP8 ~85GB; 4-bit ~47-54GB | **Top pick.** Purpose-built coding-agent MoE; ~336 tok/s FP8; >1400 tok/s prefill at 256K. |
| **Ornith-1.0-35B** ⭐ (MoE) | 35B / ~3B | long | SOTA open @ size (TB-2.1, SWE-Bench, **OpenClaw**) | **Agentic-coding-native** (self-scaffolding RL, tool-calling, `<think>`) | FP8/Q8 ~35-37GB; Q4 ~21GB (fits 5090 too) | **Strong fit / phase-1 candidate.** Built on Gemma4/Qwen3.5 (**attention-native → keeps RadixAttention prefix cache**), MIT, benchmarked *on your harness*. ⚠ brand-new (Jun 25 2026), vendor-reported scores — verify. GGUF = llama.cpp/Ollama; use safetensors/FP8 for SGLang. |
| **GLM-4.6-Air** | ~106-110B / ~12B | 200K | Strong all-round | **Best-in-class** (GLM tops BFCL ~76.7%) | FP8 tight but fits | Quality-upgrade option; more active params → smarter, slower. Use *Air* (full GLM-4.6/5.x don't fit). |
| **gpt-oss-120B (MXFP4)** | 120B / ~5.1B | 128K | Good fast gen | Moderate multi-step | ~59-67GB; only 96GB prosumer card loads it + full ctx | Apache-2.0; >200 tok/s. Great fast inner-loop / 2nd opinion. Verify native MXFP4 kernels on sm_120. |
| **Qwen3.5-122B (MTP)** | 122B / MoE | 262K | Strong | Strong | Fits VRAM, ~146 tok/s @64K | "Native VRAM-scaling champion" on 96GB; generalist+coding. |
| **Kimi-Dev-72B** | 72B dense | ~128K | **SOTA SWE-bench** for its size | Good | 4-bit ~40GB; fits | Coding-specialist; dense (slower than the MoEs) but very strong on bug-fix tasks. |
| **Nemotron 3 Super** | 120B / ~12B | 1M (262K BF16; 1M needs NVFP4) | ~60.5% SWE-bench Verified | Strong (NVIDIA agentic-tuned) | NVFP4 ~60-65GB; fits | Hybrid **Mamba-Transformer** MoE, Blackwell-native NVFP4. ⚠️ Mamba state may **not reuse your shared prefix** under RadixAttention — validate prefix-cache hit rate. 12B active → smarter but slower than A3B models. |

**Recommendation for the 96GB serve: Qwen3-Coder-Next 80B-A3B (FP8, 128-256K ctx).** Keep GLM-4.6-Air as the quality option and gpt-oss-120B as a fast alternate.

---

## Tier 2 — Fits the 32GB RTX 5090 (helper / overflow / draft / second model)
| Model | Params | Context | Coding | Agentic | Fit @32GB | Notes |
|---|---|---|---|---|---|---|
| **Devstral Small 24B** ⭐ | 24B dense | 128K | ~68% SWE-bench Verified | **Built for SWE agents** | Q4-Q5 ~14-22GB | Mistral's agentic coding model; highest SWE-bench of anything that fits a single consumer GPU. Best small *agentic-coding* pick. |
| **Qwen3-Coder 30B-A3B** | 30B / ~3B | 256K | Strong (pure coding) | Good | Q4 ~18GB | Community top pick for 32GB coding; fast (~40-55 tok/s). |
| **Qwen3.5 27B** | 27B | long | Strong | Strong reasoning | FP8/4-bit fits | General reasoning + coding. |
| **Holo3-35B-A3B** | 35B / ~3B | long | Decent | **#1 open agentic (BenchLM 82.6)** | A3B → fits easily | If you want a pure *tool-use/agent* helper over a coder. |
| **Nemotron 3 Nano 4B** | 4B | long | Light | Tool-calling tuned | tiny | NVIDIA's RTX-PC model; very high throughput → good **draft model / cheap high-fan-out helper** on the 5090. |

**Recommendation for the 5090: Devstral Small 24B** (agentic coding) or **Qwen3-Coder 30B-A3B** (pure coding throughput) — pinned as a second endpoint behind the router; toggle off for gaming.

---

## Tier 3 — Frontier coders that DON'T fit one card (cloud-scale; reference / orchestrator)
These top the 2026 leaderboards but are 400B-1.6T+ — they're your *cloud orchestrator* class, not local. Listed so you know what you're delegating *from*.

| Model | Scale | Coding | License | Why not local |
|---|---|---|---|---|
| **Qwen3-Coder-480B-A35B** | 480B/35B | 69.6% SWE-bench Verified | Apache-2.0 | 35B active; needs multi-GPU/H200-class |
| **DeepSeek-V3.2 / V4** | 600B-700B+ | ~70% | MIT | Too big for 96GB |
| **Kimi K2.6** | ~1T MoE | 71.6% agentic; 58.6 SWE-Bench Pro | OW | Too big |
| **MiniMax M3** (Jun 2026) | OW MoE | tops open SWE-Bench Pro 59.0% | OW | 1M ctx + multimodal; too big |
| **GLM-5.2** | ~744B | 62.1 SWE-Bench Pro (> GPT-5.5) | OW | Too big; use GLM-4.6-Air locally |

---

## How this maps to your architecture
- **Orchestrator (cloud, unchanged):** frontier model — owns the 350K-median/1M-token context tail and the judgment-heavy 20% of calls. The Tier-3 list is the open equivalent if you ever want an open orchestrator, but cloud Opus is the pragmatic choice.
- **Specialist (local 96GB):** **Qwen3-Coder-Next 80B-A3B** @ FP8, 128-256K ctx, behind the router. Covers ~90-99% of your subagent context profile.
- **Helper (local 32GB / 5090):** **Devstral Small 24B** or **Qwen3-Coder 30B-A3B** for overflow waves / fast tool-turns; toggles for gaming.

## Caveats
- Scores are reported by vendor/aggregator leaderboards (SWE-bench, SWE-Bench Pro, BFCL, tau-bench) and shift fast; verify on your own tasks.
- Every "fits" assumes FP8/4-bit on sm_120 — confirm the engine has working kernels for that quant (see blueprint §3) and run the pre-flight before trusting.
- "Agentic" leaders (BFCL/tau-bench) and "coding" leaders (SWE-bench) are different lists; for your specialist tier weight the *agentic-coding* intersection (Qwen3-Coder-Next, Devstral, GLM).

## Sources
- Best open-source coding models 2026 (SWE-bench rankings) — Kilo https://kilo.ai/open-source-models · Morph https://www.morphllm.com/best-open-source-llm · Siliconflow https://www.siliconflow.com/articles/en/best-open-source-LLMs-for-coding
- Agentic / tool-use leaderboards (BFCL, tau-bench, agentic) — BenchLM https://benchlm.ai/llm-agent-benchmarks · MindStudio agentic-coding 2026 https://www.mindstudio.ai/blog/best-open-source-llms-agentic-coding-2026 · Best models for OpenClaw (DeepInfra) https://deepinfra.com/blog/best-models-openclaw-agentic-workloads
- 96GB fit/throughput — Hardware-Corner (Qwen3-Coder-Next) https://www.hardware-corner.net/qwen3-coder-next-hardware-requirements/ · gpt-oss-120B on RTX PRO 6000 https://www.hardware-corner.net/guides/rtx-pro-6000-gpt-oss-120b-performance/ · Best local LLMs for 96GB https://localllm.in/blog/best-local-llms-96gb-vram
- 32GB / RTX 5090 coding models — BSWEN https://docs.bswen.com/blog/2026-03-17-best-local-llm-rtx-5090-coding/ · ModelFit https://modelfit.io/gpu/rtx-5090/ · Devstral (Mistral) coverage via Kilo/Local AI Master https://localaimaster.com/models/best-local-ai-coding-models

### Changelog
- **2026-06-26** — Created. Categorized 2026 OSS models by agentic vs coding and by rig fit (96GB / 32GB / cloud-scale), mapped to the orchestrator+specialist+helper roles.
