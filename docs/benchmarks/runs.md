# Benchmark run catalog

This manually maintained catalog indexes retained, decision-relevant runs.
`Measured hardware` names only the device that executed the workload.
`PRO relationship` prevents a co-resident or protected PRO 6000 from being
reported as measured.

## RTX PRO 6000 runs

| Date | Capability | Exact model/configuration | Measured hardware | Evidence | Decision | Dossier / finding |
|---|---|---|---|---|---|---|
| 2026-07-28 | Primary LLM | Qwen3.5 122B A10B NVFP4, rev `98915d837c4e7c87ac8296d02e89de19b3207e6d`, BF16 KV, 262K, c1 | RTX PRO 6000 | `functional`, `capacity`, `quality` | `current` | [Qwen3.5](models/qwen35-122b.md) · [qualification](../findings/2026-07-28-qwen35-122b-primary-qualification.md) |
| 2026-07-27 | Challenger LLM | Agents-A1, rev `addff08f1653ee72765c5cf458fe84556bb34f8e`, thinking disabled | RTX PRO 6000 | `functional`, `capacity`, `quality` | `challenger`, `no-promotion` | [Agents-A1](models/agents-a1.md) · [release sweep](../findings/2026-07-27-anvil-serving-release-readiness-sweep.md) |
| 2026-07-27 | Heavy control | Laguna S 2.1 NVFP4, rev `07614121b31898586430f189d27a25a0be310843` | RTX PRO 6000 | `functional`, `capacity`, `quality` | `rollback` | [Laguna](models/laguna-s-2.1.md) · [release sweep](../findings/2026-07-27-anvil-serving-release-readiness-sweep.md) |
| 2026-07-26 | Heavy LLM | Laguna S 2.1 NVFP4, pinned nightly, FP8 KV, 262K, thinking disabled | RTX PRO 6000 | `functional`, `capacity`, `quality` | `rollback` | [Laguna](models/laguna-s-2.1.md) · [qualification](../findings/2026-07-26-laguna-s-heavy-qualification.md) |
| 2026-07-18 | Heavy LLM | GPT-OSS Puzzle 88B, rev `9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`, MXFP4/FP8 KV, 131K | RTX PRO 6000 | `functional`, `capacity`, `quality` | `rollback` | [Puzzle](models/gpt-oss-puzzle-88b.md) · [enablement](../findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md) |
| 2026-07-17 | Heavy LLM | GPT-OSS Puzzle 88B compatibility and qualification sequence | RTX PRO 6000 | `compatibility-only`, `functional` | `no-promotion` at that point | [Puzzle](models/gpt-oss-puzzle-88b.md) · [qualification](../findings/2026-07-17-gpt-oss-puzzle-qualification.md) |
| 2026-07-17 | Heavy LLM | Gemma 4 31B official QAT W4A16, vLLM 0.25.1, FP8 KV | RTX PRO 6000 | `functional`, `capacity`, `quality` | `rejected` for latency | [Gemma 4](models/gemma-4.md) · [optimization](../findings/2026-07-17-gemma4-31b-optimization.md) |
| 2026-07-16 | Heavy LLM | Gemma 4 official/Unsloth 12B, 26B, 31B configurations | RTX PRO 6000 | `compatibility-only`, `capacity`, `quality` | `no-promotion` / `rejected` | [Gemma 4](models/gemma-4.md) · [vLLM 0.25.1 sweep](../findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md) |
| 2026-07-16 | Heavy LLM | Gemma 4 Unsloth NVFP4 follow-up | RTX PRO 6000 | `capacity`, `quality` | `no-promotion` | [Gemma 4](models/gemma-4.md) · [follow-up](../findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md) |
| 2026-07-16 | Heavy LLM | Gemma 4 chat-template and size bakeoff | RTX PRO 6000 | `compatibility-only`, `quality` | `no-promotion` / `rejected` | [Gemma 4](models/gemma-4.md) · [template bakeoff](../findings/2026-07-16-gemma4-chat-template-bakeoff.md) |
| 2026-07-13 | Heavy LLM | Qwen3.6 27B container recipe and first characterization | RTX PRO 6000 | `functional`, `capacity` | `no-promotion` | [Qwen3.6](models/qwen36-27b.md) · [recipe](../findings/2026-07-13-q36-pro6000-container-recipe.md) |
| 2026-07-12 | Heavy LLM | ThinkingCap Qwen3.6 27B FP8, rev `e48255afd77b403446332be0f595868337b36591` | RTX PRO 6000 | `functional`, `capacity`, `quality` | historical control | [Qwen3.6](models/qwen36-27b.md) · [promotion-era record](../findings/2026-07-12-thinkingcap-heavy-promotion.md) |
| 2026-07-12 | Heavy LLM | Qwen3.6 27B community NVFP4+MTP, official FP8, Unsloth NVFP4, ThinkingCap | RTX PRO 6000 | `functional`, `capacity`, `quality` | `no-promotion` | [Qwen3.6](models/qwen36-27b.md) · [variation bakeoff](../findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md) |
| 2026-07-12 | Heavy LLM | Qwen3.6 protocol-v2 comparison | RTX PRO 6000 | `quality` | `no-promotion` | [Qwen3.6](models/qwen36-27b.md) · [comparison](../findings/2026-07-12-qwen36-protocol-v2-comparison.md) |
| 2026-07-12 | Heavy LLM | Qwen3.6 baseline | RTX PRO 6000 | `functional`, `quality` | `no-promotion` | [Qwen3.6](models/qwen36-27b.md) · [baseline](../findings/2026-07-12-qwen36-27b-eval-baseline.md) |
| 2026-07-12 | Heavy LLM | Qwen3.5 122B MXFP4, rev `345839ea666a70f5035672f7c88afcba6281921f` | RTX PRO 6000 | `functional`, `capacity`, `quality` | `no-promotion` | [Qwen3.5](models/qwen35-122b.md) · [MXFP4 benchmark](../findings/2026-07-12-qwen35-122b-mxfp4-benchmark.md) |
| 2026-07-12 | Heavy LLM | Nemotron 3 Super 120B and Mistral Small 4 | RTX PRO 6000 | `functional`, `capacity`, `quality` | `no-promotion` | [Nemotron Super](models/nemotron3-super-120b.md), [Mistral](models/mistral-small-4.md) · [challengers](../findings/2026-07-12-heavy-intelligence-challengers.md) |
| 2026-07-12 | Heavy LLM | Nemotron Puzzle 75B recheck | RTX PRO 6000 | `functional`, `capacity`, `quality` | `no-promotion` | [Nemotron Puzzle](models/nemotron-puzzle-75b.md) · [recheck](../findings/2026-07-12-nemotron-puzzle-recheck.md) |
| 2026-07-12 | Heavy LLM | GPT-OSS 120B deterministic recheck | RTX PRO 6000 | `capacity`, `quality` | `no-promotion` | [GPT-OSS 120B](models/gpt-oss-120b.md) · [recheck](../findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md) |
| 2026-07-12 | Heavy LLM | Laguna XS 2.1 protocol-v2 attempts | RTX PRO 6000 | `historical-invalid`, `compatibility-only` | `rejected` | [Laguna](models/laguna-s-2.1.md) · [eval v2](../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md) |
| 2026-07-10–11 | Heavy LLM | Ornith 35B FP8, MiniMax M2.7 REAP, DeepSeek V4 Flash, Nemotron Puzzle, Qwen3.6 NVFP4+MTP | RTX PRO 6000 | `functional`, `capacity`, `quality`, `historical-invalid` | `no-promotion` / `rejected` | [Dossiers](models/index.md#rtx-pro-6000) · [Blackwell bakeoff](../findings/2026-07-10-blackwell-local-model-bakeoff.md) |
| 2026-07-10 | Heavy LLM | Qwen3.5 122B NVFP4, NGC 26.04, FP8 KV, 131K | RTX PRO 6000 | `functional`, `capacity`, `quality` | `no-promotion` at that point | [Qwen3.5](models/qwen35-122b.md) · [candidate record](../findings/2026-07-10-qwen35-122b-heavy-candidate.md) |

## RTX 5090 runs

| Date | Capability | Exact model/configuration | Measured hardware | PRO relationship | Evidence | Decision | Dossier / finding |
|---|---|---|---|---|---|---|---|
| 2026-07-28 | STT | Parakeet TDT 0.6B v3 baseline | RTX 5090 | `protected/co-resident` | `quality`, `capacity` | `current` | [Parakeet](models/parakeet.md) · [ASR qualification](../findings/2026-07-28-nemotron35-asr-qualification.md) |
| 2026-07-28 | STT | Qwen3-ASR 0.6B, rev `5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | RTX 5090 | `protected/co-resident` | `quality`, `capacity` | `challenger`, `no-promotion` | [Qwen3-ASR](models/qwen3-asr.md) · [ASR qualification](../findings/2026-07-28-nemotron35-asr-qualification.md) |
| 2026-07-28 | STT | Nemotron 3.5 ASR, rev `f3d333391852ba876df169dcc9ba902d25b6ab0b` | RTX 5090 | `protected/co-resident` | `quality`, `capacity` | `rejected` | [Nemotron ASR](models/nemotron35-asr.md) · [ASR qualification](../findings/2026-07-28-nemotron35-asr-qualification.md) |
| 2026-07-27 | Omni/vision | Nemotron Nano Omni 30B NVFP4, rev `dc5f0b0bfddf8b6e0f5891475be9af05b80126fe` | RTX 5090 | `protected/co-resident` | `functional`, `capacity` | `current` topology | [Nemotron Omni](models/nemotron-omni-30b.md) · [Omni qualification](../findings/2026-07-27-omni-stack-qualification.md) |
| 2026-07-27 | Omni/voice | Qwen2.5-Omni 3B, rev `f75b40e3da2003cdd6e1829b1f420ca70797c34e`; Parakeet; Kokoro | RTX 5090 | `protected/co-resident` | `functional`, `capacity` | `challenger`, `no-promotion` | [Qwen Omni](models/qwen25-omni-3b.md), [Parakeet](models/parakeet.md), [Kokoro](models/kokoro.md) · [co-resident stack](../findings/2026-07-27-omni-voice-stack-qualification.md) |
| 2026-07-16 | Fast LLM | Gemma 4 E4B and controls | RTX 5090 | `topology-only` | `quality`, `capacity` | historical control | [Gemma E4B](models/gemma4-e4b.md) · [template bakeoff](../findings/2026-07-16-gemma4-chat-template-bakeoff.md) |
| 2026-07-13 | Fast LLM | Gemma 4 E4B Fast | RTX 5090 | `topology-only` | `functional` | historical `current` | [Gemma E4B](models/gemma4-e4b.md) · [router promotion](../findings/2026-07-13-e4b-fast-router-promotion.md) |
| 2026-07-10–11 | Fast/Omni | Nemotron Nano/Omni 30B, Gemma 4 31B failed load, Qwen3.5 35B GGUF, Gemma E4B GGUF | RTX 5090 | `topology-only` | `functional`, `capacity`, `quality`, `historical-invalid` | `no-promotion` / `rejected` | [RTX 5090 dossiers](models/index.md#rtx-5090) · [Blackwell bakeoff](../findings/2026-07-10-blackwell-local-model-bakeoff.md) |

## Related views

- [RTX PRO 6000 hardware page](hardware/rtx-pro-6000.md)
- [RTX 5090 hardware page](hardware/rtx-5090.md)
- [Model dossiers](models/index.md)
- [RTX PRO 6000 mention audit](rtx-pro-6000-audit.md)
- [Chronological findings](../findings/README.md)
