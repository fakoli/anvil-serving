# RTX PRO 6000 benchmark view

**Hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition,
96 GB each (192 GB aggregate), sm_120. **Host:** Fakoli Dark, Windows 11 with
Docker Desktop/WSL2. **Reviewed:** 2026-08-03.

> Side-by-side speed and recipe links for every configuration measured on this
> card or both cards in TP=2: [model comparison table](../comparison.md).

This page contains only measurements made on one or both PRO 6000 cards. Tests
that merely kept a card running or described its topology belong in the
[mention audit](../rtx-pro-6000-audit.md), not the result tables.

## Current topology and services

The two GPU roles are symmetric. Split mode can place independent workloads on
the cards. Exclusive TP=2 mode assigns both roles to one declared owner and
blocks every other inference workload until the mode is left; the cards are
connected over PCIe without NVLink, so 192 GB is aggregate rather than unified
memory. The human-approved 2026-08-02 promotion assigns both roles to the
DeepSeek 650K Primary. Fakoli Mini remains model-free and reaches it remotely.

## Current, rollback, and challenger state

| Order | Model | Decision | Contract |
|---:|---|---|---|
| 1 | [DeepSeek V4 Flash 0731](../models/deepseek-v4-flash.md) | `current` | Exclusive TP=2 text Primary; 650K context; high reasoning default; 32,768 output cap |
| 2 | [Qwen3.5 122B](../models/qwen35-122b.md) | `rollback` | Immediate managed rollback; image/OCR; 240K retrieval |
| 3 | [Agents-A1](../models/agents-a1.md) | previous Primary | Retained FP8 text/image/video recipe; thinking disabled; 240K retrieval |
| 4 | [Laguna S 2.1](../models/laguna-s-2.1.md) | `rollback` | Additional managed rollback; thinking disabled |
| 5 | [GPT-OSS Puzzle 88B](../models/gpt-oss-puzzle-88b.md) | `rollback` | Additional pinned rollback; strict unified-diff caveat |

## Comparable quality and context

### Exclusive dual-card TP=2, 2026-08-01

| Candidate | Repeated quality | Capacity and context evidence | Decision |
|---|---|---|---|
| Qwen3.5 122B NVFP4 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 2.32 s TTFT and 67.5 tok/s decode; 128K 4/4 at 14.59 s and 65.0 tok/s | TP=2 `no-promotion`; single-card profile remains `rollback` |
| Nemotron 3 Super 120B NVFP4, TP=2 + EP=2 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 2.84 s and 59.5 tok/s; 60K 4/4 at 5.58 s and 60.0 tok/s | `no-promotion` |
| Laguna S 2.1 NVFP4 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 1.97 s and 70.9 tok/s; 240K 4/4 at 31.85 s and 66.0 tok/s | TP=2 `no-promotion`; single-card profile remains `rollback` |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5 | coding/intelligence/session/tools 27/27; low/high/max functional gates pass | 128K pass; warmed 125,785-token row 19.44 s TTFO, 23.81 s visible TTFT, 128.9 tok/s decode; matched 4K decode 130.7 tok/s | priority intelligence `challenger`, `no-promotion`; DSpark preferred for experiments, both lanes fail 3 GiB reserve |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5, GPU-only Pi contexts | 650K/maxseq16 passed the low-reasoning gate plus Dark Pi, Mini Pi, and Mini OpenClaw high-reasoning smokes; 1M retained two fatal client-shaped workspace failures | 640K retrieval 120.6 s; matched 32K decode 141.6 tok/s at 650K; 1M qualification reached 985K before later client failures | 650K `current`, human-approved Primary; 1M experimental only; 3 GiB reserve explicitly waived |
| DeepSeek V4 Flash 0731, remote AI-MBP25 benchmark worker | 8K context 1/1; tool-error retry protocol passed but final answer failed; SWE-bench Verified official-grader smoke resolved 1/1 | 6,102 observed prompt tokens in the 8K context case; larger buckets not attempted | benchmark substrate qualified for scout; no promotion change |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5 + native KV offload | full functional preflight passes; 128K and 256K CPU reload proven | 8 GiB cold 249,573-token row: 43.75 s TTFO, 45.58 s visible TTFT, 5,705 effective prefill tok/s, 135.2 tok/s decode; 16 GiB exact 113,674-token replay: 113,408 external hits, 1.002 GB CPU-to-GPU, 0.825 s TTFO; managed mmap cleanup passes | capacity extension, `no-promotion`; no 256K per-card reserve sample |
| DeepSeek V4 Flash 0731, earlier SGLang lane | intelligence 6/6, session 3/3, tools 3/3 at low reasoning | 32K 11/12; 2.70 s TTFO, 29.11 s first-visible TTFT, 11.5 tok/s combined reasoning/visible decode | retained low-reasoning point-in-time lane; one reasoning-only exhaustion; see r16 row for current DSpark/high/max evidence |
| Inkling Small NVFP4 | intelligence 6/6, session 3/3, tools 3/3 at low reasoning | 32K 12/12; 2.79 s TTFO, 4.63 s first-visible TTFT, 73.5 tok/s combined reasoning/visible decode; reasoning-off lane also 12/12 | `no-promotion`; reasoning-off Responses caveat retained |

All rows used both physical cards as measured hardware, exclusive ownership,
one admitted request, and no co-resident inference. See the
[dated campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md) for
exact revisions, raw artifacts, protocol differences, and failure records.

#### DeepSeek 0731 research priority and r16 result

DeepSeek identifies 0731 as a re-post-trained official Flash generation with
the same 284B/13B-active target architecture as Preview and a bundled DSpark
draft module. Artificial Analysis independently scores the max-effort model at
50, number 3 of 101 comparable models, but reports 210 million evaluation
output tokens. That verbosity signal is consistent with the earlier 11/12
run's reasoning-only exhaustion and makes reasoning-budget policy a first-class
capacity gate.

The follow-up translated a pinned r16 B12X/InstantTensor recipe to WSL2 and
qualified the official release revision at 131K with DSpark K5. Low, high, and
max reasoning preflights passed, as did a 27/27 coding-agent slice and a warmed
125,785-prompt-token request. Against the same image and checkpoint without
speculative decoding, DSpark improved median per-request decode by 101.4% and
reduced median E2E by 58.8%. It also consumed 1.6-2.3 GiB more VRAM, and neither
lane retained 3 GiB reported free on each card. See the
[r16 qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md).

Current 0731-specific NVFP4 conversions are community artifacts. The strongest
conversion receipt reports TP=2 generation on two DGX Spark systems; the
strongest RTX PRO performance report uses four cards and an out-of-tree DSpark
path. Neither proves fit, quality, or speed on this two-card topology. See the
[research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
for pinned identities, benchmark deltas, conversion recipes, GGUF sizes, and
the publisher-reasoning/DSpark/NVFP4 qualification sequence.

### Single-card and historical profiles

| Candidate | Repeated quality | Context evidence | Decision |
|---|---|---|---|
| Qwen3.5 122B NVFP4 | Passed protocol-v3, tools 10/10, image/OCR | 128K and 240K retrieval; 262,144 served | `rollback` |
| Laguna S 2.1 NVFP4 | Passed protocol-v3 with thinking disabled | 32K/128K/240K passed; TTFT 2.26/21.15/50.64 s | `rollback` |
| GPT-OSS Puzzle 88B | Tools/session/timeout 3/3; unified diff 2/3 | 32K and 128K retained | `rollback` |
| Agents-A1 | Official FP8 passed three-repetition protocol-v3; BF16/FP8 retain the identical 28/30 multimodal corpus | 240K on a 262,144 serve; 35.21 s promotion-quality TTFT, 32.97 s capacity TTFT p50 at 231,426 actual tokens | `current` |
| Gemma 4 12B QAT W4A16 | Historical protocol-v3 control passed | 240K passed | `no-promotion` |
| ThinkingCap Qwen3.6 27B FP8 | Historical strict-quality control passed | 240K retained | `no-promotion` |

## Capacity and recipe comparisons

| Model/configuration | Served context | Admission | Capacity note |
|---|---:|---:|---|
| Agents-A1 FP8 multimodal | 262,144 | c1 at 262K; earlier 131K c32 | 188 tok/s decode at 8K c1; 156 tok/s decode and 32.97 s TTFT at 240K; 51.93 GiB KV; generated MoE tune rejected |
| Agents-A1 NVFP4 compact text | 131,072 | 16 | 198 tok/s at 8K c16; 128K c4 pass; vision excluded |
| Qwen3.5 122B NVFP4 | 262,144 | 1 | BF16 KV; near-ceiling prefill is slow |
| Laguna S 2.1 NVFP4 | 262,144 | recorded recipe | FP8 KV; disabled-thinking contract |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5, TP=2 | 131,072 | 8 configured; c1 measured | FP8 MLA KV; 130.7 tok/s matched decode; 128K pass; 3 GiB reserve failed |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5 GPU-only Pi, TP=2 | 650,000 current / 1,000,000 experimental | 16 | 650K: 640K retrieval, 141.6 tok/s matched 32K decode, live Pi/OpenClaw smokes; 1M: retained client-shaped workspace failures; reserve waived |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5 + native offload, TP=2 | 262,144 | 8 configured; c1 measured | 8 GiB cold 250K capacity; 16 GiB CPU tier proves 113,408-token external reload; per-card reserve not sampled |
| GPT-OSS Puzzle 88B MXFP4 | 131,072 | 8 | FP8 KV; pinned Anvil vLLM |
| Nemotron 3 Super 120B NVFP4 | 131,072 | 5 | 1M advertised is not locally validated |
| Qwen3.6 27B community NVFP4 + MTP | 262,144 | 5 | 262K needle validated |
| Mistral Small 4 119B NVFP4 | 131,072 | 5 | Low short-request TTFT; weaker quality slice |

## Recent changes

- 2026-08-03: AI-MBP25 completed the first managed remote context, agentic
  recovery, and SWE-bench Verified smoke against the unchanged 650K DeepSeek
  Primary. The 8K context and one-instance official grader paths passed. The
  tool-error case retried correctly but failed its final answer. This qualifies
  the worker and artifact path for a scout campaign without changing routes.
- 2026-08-02: after human approval, the 650K/maxseq16 profile became
  `llm.primary` with high reasoning as the client default and a generic
  per-tier 32,768 output cap. Dark Pi, Mini Pi, and Mini OpenClaw passed. The
  1M/maxseq16 profile was removed after two real client-shaped B12X workspace
  crashes, including one with only 5,120 requested output tokens.
- 2026-08-02: moving display output to the AMD iGPU allowed the DeepSeek 0731
  maxseq16 envelope to start. GPU-only 650K/maxseq16 and 1M/maxseq4/maxseq16
  passed near-limit retrieval and Pi protocol gates. The 650K profile is the
  preferred everyday Pi experiment; 1M/maxseq16 is the preferred explicit
  deep-session alternative. A retained
  1M/maxseq1 B12X workspace crash and sub-1-GiB post-workload free VRAM keep all
  profiles `no-promotion`.
- 2026-08-02: the derived DeepSeek 0731 WSL2 native-offload lane qualified a
  262,144-token serve through a 249,573-prompt-token request. A 16 GiB CPU tier
  also reloaded a 113,408-token external prefix after GPU eviction. An
  ownership-aware Anvil lifecycle now blocks cleanup while workers map the
  file and reclaims both 8 and 16 GiB mmaps after teardown. The decision remains
  `no-promotion`.
- 2026-08-01: the pinned DeepSeek 0731 r16 B12X lane qualified DSpark K5,
  low/high/max reasoning, 128K, and 27/27 coding-agent attempts. DSpark doubled
  matched decode versus same-image no-spec, but both lanes failed the 3 GiB
  reserve and remain `no-promotion`.
- 2026-08-01: DeepSeek V4 Flash 0731 became the priority intelligence
  challenger after official and independent research was reconciled with its
  exact local low-reasoning TP=2 evidence. The decision remains
  `no-promotion`; no new GPU run or route change occurred in the research pass.
- 2026-08-01: the hardware became a symmetric two-PRO topology. The exclusive
  TP=2 campaign qualified Qwen3.5, Nemotron 3 Super, Laguna S, DeepSeek V4
  Flash 0731, and Inkling Small without changing production aliases.
- 2026-07-29: Agents-A1 official FP8 passed the missing three-repetition
  protocol-v3 suite at the 262K profile and was promoted through the managed
  transaction. Qwen3.5 is now the immediate rollback.
- 2026-07-29: Agents-A1 official FP8 passed the same 262K/240K functional
  and capacity shape as Qwen. It used 35.31 versus 73.22 GiB model memory,
  halved 240K TTFT, and delivered the unchanged video corpus. Qwen passed all
  images but its exact NGC image lacked an H.264 decoder. Agents-A1 wins the
  bounded comparison; Qwen remains Primary pending matched repeated quality.
- 2026-07-28: Agents-A1 BF16/FP8 image and direct-video capability passed,
  but both reached only 28/30 on the strict multimodal corpus. NVFP4 qualified
  as a compact text-only profile. Isolated routed video passed after bounded
  thinking/error-classification fixes, and the FP8 tune was rejected; no route
  changed.
- 2026-07-28: Qwen3.5 122B became the human-gated Primary; Laguna moved to
  immediate rollback.
- 2026-07-27: Agents-A1 qualified as a thinking-disabled challenger without
  promotion.
- 2026-07-26: Laguna S 2.1 passed repeated quality and 240K retrieval.
- 2026-07-18: Puzzle established the pinned secondary recipe and strict-format
  caveat.

## Run history

The complete PRO 6000 history, including failed loads and incomplete runs, is
in the [run catalog](../runs.md#rtx-pro-6000-runs). Every row links its dated
finding and a stable [model dossier](../models/index.md).
