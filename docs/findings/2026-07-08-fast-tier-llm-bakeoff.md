# Fast-Tier LLM Bakeoff Plan and Candidate Registry

Date: 2026-07-08

This finding anchors the Anvil PRD `fast-tier-llm-bakeoff-2026-07`. It is the source-backed
candidate registry and scoring rubric for the long-running RTX 5090 Fast-tier bakeoff. The
entries below are priors, not promotion evidence, until a local Fakoli Dark run records live
health, preflight, voice, context, tool, and benchmark artifacts.

## Operating Constraints

- Fakoli Mini stays model-free for reference OpenClaw Talk validation. It may run OpenClaw Gateway,
  Anvil Voice Realtime/proxy, Claude Code, and Codex, but not STT, TTS, or LLM model serves.
- Fakoli Dark owns the RTX 5090 Fast candidate serves, router, STT/TTS endpoints, and benchmark
  execution.
- Heavy is not part of this bakeoff and must remain available for larger, more capable models.
- Reddit and community posts are recipe priors only. Official model cards and engine docs are the
  source of truth for model facts; local benchmark artifacts are the source of truth for promotion.
- A candidate may test at most three engine/quantization variants. Prefer a small number of
  well-sourced runs over broad, low-signal churn.

## Candidate Matrix

| Candidate | Required | Primary role | Official source | Community or recipe prior | Initial variants |
|---|---:|---|---|---|---|
| `nvidia/Qwen3.6-27B-NVFP4` | yes, control | Current Fast baseline | HF + vLLM recipe | RTX 5090 vLLM/MTP Reddit runs | current vLLM 32K, vLLM long-context, llama.cpp/GGUF only if a matching quant is sourced |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | yes | Qwen MoE quality/context candidate | HF + vLLM recipe | HF/Reddit release thread | vLLM NVFP4 32K, vLLM 64K/128K if memory allows, SGLang only if recipe is verified |
| `nvidia/Gemma-4-31B-IT-NVFP4` | yes | dense/near-dense Gemma candidate | HF + NVIDIA NIM card | RTX PRO/Gemma NVFP4 Reddit reports | vLLM NVFP4 32K with FP8 KV, vLLM longer context, llama.cpp GGUF Q5/Q6 if sourced |
| `zai-org/GLM-4.7-Flash` | yes | tool/agent MoE candidate | HF model card | Reddit GLM support/caveat threads plus vLLM/SGLang/llama.cpp article | SGLang/vLLM 32K, llama.cpp GGUF only with caveat tracking, one longer-context attempt if stable |
| `mistralai/Devstral-Small-2-24B-Instruct-2512` | yes | coding-agent/tool-use candidate | HF + Mistral Devstral page | Reddit release thread, GGUF card, Unsloth llama-server docs | vLLM FP8 32K, llama.cpp GGUF Q5/Q6, longer-context run if stable |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | optional | fallback speed/quality reference | HF model card/discussion | Qwen/GLM comparison posts | Use only if a required model cannot run or leaves a clear coverage gap |

## Scoring Rubric

Score each locally tested candidate/config on 100 points after hard gates pass:

| Category | Points | What to measure |
|---|---:|---|
| Voice latency | 30 | LLM-stage latency, TTFT/TTFA, total STT-to-LLM-to-TTS turn latency, repeated-turn stability |
| Intelligence and tool quality | 30 | coding/editing prompts, tool-call schema adherence, practical instruction following, Haiku-like usefulness |
| Usable context | 15 | accepted context target, 32K/64K/128K sweep result, long-context retrieval/needle behavior |
| Agent and multi-turn reliability | 15 | session memory, transcript behavior, no empty/repeated/thinking-only responses, stable tool history |
| Operational fit | 10 | load time, VRAM headroom, health behavior, recipe reproducibility, rollback safety |

Hard gates before scoring:

- Loads on Fakoli Dark's RTX 5090.
- Does not disrupt Heavy.
- Completes at least one OpenClaw/Anvil Voice cycle.
- Supports usable tool calls.
- Preserves session/chat behavior.
- Restores the production Fast baseline after disruptive experiments.

Promotion rule: promote only if the candidate beats the current `nvidia/Qwen3.6-27B-NVFP4`
baseline on total score, passes every hard gate, and does not introduce unacceptable voice,
tool, context, or operational regressions. Otherwise keep the current baseline and mark the best
alternate as verified/non-promoted or needs-more-data.

## Source Classes

Use these source labels in `configs/serve-recipes.toml` and final evidence:

- `official`: Hugging Face model card, vendor page, or engine recipe docs.
- `community-prior`: Reddit, blog, forum, model discussion, or third-party recipe that still
  requires local verification.
- `locally-verified`: Fakoli Dark evidence captured by `anvil-serving` benchmark, voice, preflight,
  serve status, and log artifacts.

## Sources

- Qwen3.6-27B NVFP4 official/model recipe:
  <https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4>,
  <https://recipes.vllm.ai/Qwen/Qwen3.6-27B>
- Qwen3.6-27B RTX 5090 community priors:
  <https://www.reddit.com/r/LocalLLaMA/comments/1t5dya8/qwen36_27b_nvfp4_mtp_on_a_single_rtx_5090_200k/>,
  <https://www.reddit.com/r/LocalLLaMA/comments/1sv8eua/qwen3627b_at_80_tps_with_218k_context_window_on/>
- Qwen3.6-35B-A3B NVFP4 official/model recipe:
  <https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4>,
  <https://recipes.vllm.ai/Qwen/Qwen3.6-35B-A3B>
- Qwen3.6-35B-A3B community prior:
  <https://www.reddit.com/r/LocalLLaMA/comments/1ts6j6j/nvidiaqwen3635ba3bnvfp4_hugging_face/>
- Gemma-4-31B-IT-NVFP4 official sources:
  <https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4>,
  <https://build.nvidia.com/google/gemma-4-31b-it/modelcard>
- Gemma community priors:
  <https://www.reddit.com/r/LocalLLaMA/comments/1sbivxj/gemma431b_nvfp4_inference_numbers_on_1x_rtx_pro/>,
  <https://www.reddit.com/r/LocalLLaMA/comments/1sffapb/might_be_an_amateur_question_but_how_do_i_get_the/>
- GLM-4.7-Flash official source and community priors:
  <https://huggingface.co/zai-org/GLM-4.7-Flash>,
  <https://www.reddit.com/r/LocalLLaMA/comments/1qh5wdq/zaiorgglm47flash_hugging_face/>,
  <https://www.reddit.com/r/LocalLLaMA/comments/1qih9r8/current_glm47flash_implementation_confirmed_to_be/>,
  <https://agentnativedev.medium.com/glm-4-7-flash-on-24gb-gpu-llama-ccp-vllm-sglang-transformers-b3358d2f0e78>
- Devstral Small 2 official and community priors:
  <https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512>,
  <https://mistral.ai/news/devstral/>,
  <https://www.reddit.com/r/LocalLLaMA/comments/1piabn8/devstralsmall224binstruct2512_on_hugging_face/>,
  <https://huggingface.co/bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF>,
  <https://unsloth.ai/docs/basics/inference-and-deployment/llama-server-and-openai-endpoint>
- Optional Qwen3-30B-A3B fallback:
  <https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507>,
  <https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507/discussions/24>
- llama.cpp/OpenAI-compatible tool-call support:
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md>,
  <https://llama-cpp-python.readthedocs.io/en/latest/server/>
