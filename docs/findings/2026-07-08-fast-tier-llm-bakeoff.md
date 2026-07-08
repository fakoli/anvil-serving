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

## Live Results

Evidence directory:
[`docs/findings/fast-tier-bakeoff-evidence/`](fast-tier-bakeoff-evidence/)
Typed summary:
[`t006-evidence-summary.json`](fast-tier-bakeoff-evidence/t006-evidence-summary.json)

All voice runs used Fakoli Dark audio endpoints over the private address and
recorded `mini_model_free_assertion.passed=true`; Mini was not used to host STT,
TTS, or LLM models. Final runtime restoration evidence is recorded in
[`runtime-restoration.md`](fast-tier-bakeoff-evidence/runtime-restoration.md):
Heavy used the same running container with restart count `0` and health `200`
observations during the matrix window and final handoff; production Fast was
restored to `vllm-qwen36` with health `200`; and all experimental candidate
serves were stopped or absent.

Voice artifacts in this report are stage-latency evidence. They measure STT,
LLM, and TTS timing with TTS first-audio observed, but the STT hypothesis field
is empty with WER `1.0`, so these artifacts must not be read as semantic STT
accuracy evidence.

| Candidate/config | Engine and context | Voice total / LLM stage | Bakeoff TTFT / E2E | Tool / session / intelligence | Evidence | Result |
|---|---|---:|---:|---|---|---|
| `nvidia/Qwen3.6-27B-NVFP4` control | vLLM NVFP4, 32K | 1130.21 ms / 814.83 ms | 6203.94 ms / 9041.91 ms | pass / pass / 0.50 | `qwen36-27b-baseline-vllm-32k.voice.json`, `qwen36-27b-baseline-vllm-32k.bakeoff.json` | Control rerun succeeded |
| `nvidia/Qwen3.6-35B-A3B-NVFP4` | vLLM NVFP4, 32K | 377.52 ms / 165.40 ms | 1489.36 ms / 2302.37 ms | pass / pass / 0.50 | `qwen36-35b-a3b-vllm-nvfp4-32k.voice.json`, `qwen36-35b-a3b-vllm-nvfp4-32k.bakeoff.json` | Best promotion candidate |
| `nvidia/Gemma-4-31B-IT-NVFP4` | vLLM NVFP4, 32K then 8K retry | no successful voice run | no benchmark artifact | no score | `load-failures.md` | Rejected: does not fit 5090 recipe |
| `zai-org/GLM-4.7-Flash` | SGLang BF16, 32K | no successful SGLang voice run | no SGLang benchmark artifact | no score | `load-failures.md` | Rejected for SGLang: no KV headroom |
| `zai-org/GLM-4.7-Flash` | llama.cpp `UD-Q4_K_XL`, 32K | 2376.21 ms / 961.49 ms | 6196.05 ms / 7417.46 ms | pass / pass / 0.00 | `glm47-flash-llamacpp-q4-32k.voice.json`, `glm47-flash-llamacpp-q4-32k.bakeoff.json` | Verified but not competitive |
| `mistralai/Devstral-Small-2-24B-Instruct-2512` | vLLM FP8, reduced 8K | 923.98 ms / 433.12 ms | 742.46 ms / 3755.56 ms | pass / pass / 1.00 | `devstral-small2-vllm-fp8-8k.voice.json`, `devstral-small2-vllm-fp8-8k.bakeoff.json`, `load-failures.md` | Promising fallback, context-limited |

## Rubric Score

Scores below apply the 100-point rubric to the measured configuration, not to
the model family in general. They are promotion guidance, not an automatic
router policy change.

| Candidate/config | Voice | Intelligence/tool | Context | Agent reliability | Ops fit | Total | Notes |
|---|---:|---:|---:|---:|---:|---:|---|
| `nvidia/Qwen3.6-35B-A3B-NVFP4`, vLLM NVFP4 32K | 30 | 22 | 15 | 14 | 8 | 89 | Fastest measured stage-latency path, 32K context, same deterministic intelligence miss as baseline |
| `nvidia/Qwen3.6-27B-NVFP4`, production control | 20 | 22 | 15 | 13 | 10 | 80 | Stable baseline, but much slower than 35B-A3B |
| `mistralai/Devstral-Small-2-24B-Instruct-2512`, vLLM FP8 8K | 24 | 29 | 5 | 14 | 6 | 78 | Passed the small intelligence suite, but only after reducing context to 8K and adding Mistral request fallback |
| `zai-org/GLM-4.7-Flash`, llama.cpp Q4 32K | 8 | 10 | 15 | 10 | 5 | 48 | Tool/session pass, but voice latency and deterministic intelligence failures make it unsuitable for Fast voice |
| `nvidia/Gemma-4-31B-IT-NVFP4` | 0 | 0 | 0 | 0 | 0 | 0 | No viable loaded endpoint on the 32 GB 5090 recipe |

## Determination

Recommend `nvidia/Qwen3.6-35B-A3B-NVFP4` as the next human-gated Fast-tier
promotion candidate. It beat the production `nvidia/Qwen3.6-27B-NVFP4` control
on the measured voice path and loaded-endpoint bakeoff while preserving the
same 32K context target, tool-call pass, and session-recall pass. It should not
be auto-promoted by this task because router policy promotion remains a human
gate and the deterministic intelligence suite still has one shared failure with
the current baseline.

Keep the current production Fast baseline until the promotion task explicitly
updates the deployed route. `Devstral-Small-2` is worth keeping as a reduced
context fallback candidate for agent/code behavior, but it is not a default
Fast voice replacement because the successful evidence is 8K, not 32K.
`GLM-4.7-Flash` via llama.cpp is verified but too slow for this use case.
`Gemma-4-31B-IT-NVFP4` is rejected for the RTX 5090 Fast role under the tested
vLLM recipe.

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
