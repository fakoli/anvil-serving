# RTX PRO 6000 benchmark view

**Hardware:** NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition, 96 GB,
sm_120. **Host:** Fakoli Dark, Windows 11 with Docker Desktop/WSL2.
**Reviewed:** 2026-07-29.

> Side-by-side speed and recipe links for every configuration measured on this
> card: [model comparison table](../comparison.md#rtx-pro-6000-blackwell-max-q-96-gb-sm_120-300-w).

This page contains only measurements made on the PRO 6000. Tests that merely
kept this card running or described its topology belong in the
[mention audit](../rtx-pro-6000-audit.md), not the result tables.

## Current topology and services

The card owns the router-adjacent Primary LLM serve. The current Primary is
Agents-A1 official FP8 at `http://127.0.0.1:30002/v1`, served as
`agents-a1-fp8-mm-262k`, with FP8 KV, a 262,144-token window, one admitted
sequence, four images and one video per request, and thinking hard-disabled by
the router. Fakoli Mini does not host a model.

## Current, rollback, and challenger state

| Order | Model | Decision | Contract |
|---:|---|---|---|
| 1 | [Agents-A1](../models/agents-a1.md) | `current` | FP8 text/image/video Primary; thinking disabled; 240K retrieval |
| 2 | [Qwen3.5 122B](../models/qwen35-122b.md) | `rollback` | Immediate managed rollback; image/OCR; 240K retrieval |
| 3 | [Laguna S 2.1](../models/laguna-s-2.1.md) | `rollback` | Additional managed rollback; thinking disabled |
| 4 | [GPT-OSS Puzzle 88B](../models/gpt-oss-puzzle-88b.md) | `rollback` | Additional pinned rollback; strict unified-diff caveat |
| 5 | [Gemma 4](../models/gemma-4.md) / [ThinkingCap](../models/qwen36-27b.md) | `no-promotion` | Historical strict-quality controls |

## Comparable quality and context

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
| GPT-OSS Puzzle 88B MXFP4 | 131,072 | 8 | FP8 KV; pinned Anvil vLLM |
| Nemotron 3 Super 120B NVFP4 | 131,072 | 5 | 1M advertised is not locally validated |
| Qwen3.6 27B community NVFP4 + MTP | 262,144 | 5 | 262K needle validated |
| Mistral Small 4 119B NVFP4 | 131,072 | 5 | Low short-request TTFT; weaker quality slice |

## Recent changes

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
