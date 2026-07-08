# Fast-Tier Bakeoff Load-Failure Notes

Date: 2026-07-08

These notes preserve the bounded serve-log evidence for candidate configurations
that were attempted during `fast-tier-llm-bakeoff-2026-07:T006` but did not
produce benchmark artifacts. Successful candidate evidence remains in the JSON
files in this directory.

## `nvidia/Gemma-4-31B-IT-NVFP4`

Attempted engine/configs:

- vLLM Gemma 4 support image, NVFP4, FP8 KV, 32K context.
- vLLM Gemma 4 support image, NVFP4, FP8 KV, reduced 8K context,
  `FAST_GEMMA4_MAX_NUM_SEQS=1`, and `FAST_GEMMA4_GPU_MEMORY_UTILIZATION=0.98`.

Recipe fixes discovered before the final memory failure:

- `--enable-auto-tool-choice` requires Gemma 4 parser/template flags, so the
  recipe now includes `--reasoning-parser gemma4`,
  `--tool-call-parser gemma4`, and
  `--chat-template examples/tool_chat_template_gemma4.jinja`.
- The Gemma 4 support image's model-inspection subprocess rejected the GPU UUID
  form on this host. This service now uses integer device id `0`, which the
  serve status maps to Fakoli Dark's RTX 5090.

Retained bounded log excerpt from:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml logs fast-gemma4-31b --tail 160
```

Key lines:

```text
non-default args: {... 'model_tag': 'nvidia/Gemma-4-31B-IT-NVFP4', ... 'max_model_len': 8192, 'gpu_memory_utilization': 0.98, 'kv_cache_dtype': 'fp8', 'max_num_seqs': 1}
Using max model len 8192
Resolved architecture: Gemma4ForConditionalGeneration
ValueError: Free memory on device cuda:0 (30.2/31.84 GiB) on startup is less than desired GPU memory utilization (0.98, 31.21 GiB). Decrease GPU memory utilization or reduce GPU memory used by other processes.
RuntimeError: Engine core initialization failed. See root cause above.
```

Root cause: the 31B Gemma 4 NVFP4 image does not leave practical RTX 5090
headroom on the tested host even after reducing the context target to 8K and
single sequence. It is not a viable Fast-tier candidate on the 32 GB 5090 under
this recipe.

## `zai-org/GLM-4.7-Flash` with SGLang BF16

Attempted engine/config:

- SGLang, BF16/safetensors, 32K context, `--mem-fraction-static 0.85`,
  GLM tool and reasoning parsers.

Retained bounded log excerpt from:

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.toml logs fast-glm47-flash-sglang --tail 160
```

Key lines:

```text
server_args=ServerArgs(model_path='zai-org/GLM-4.7-Flash', ... context_length=32768, mem_fraction_static=0.85, ... reasoning_parser='glm45', tool_call_parser='glm47')
Load weight begin. avail mem=30.06 GB
rope_scaling missing 'factor', defaulting to 1.0. Check model accuracy.
Load weight end. elapsed=269.29 s, type=Glm4MoeLiteForCausalLM, avail mem=0.00 GB, mem usage=30.06 GB.
ValueError: Loaded weights leave no GPU memory for the KV cache under --mem-fraction-static=0.85. Raise --mem-fraction-static above 1.000 (minimum viable = 1 - available/pre = 1.0000).
```

Root cause: the BF16 SGLang serve consumes essentially the whole RTX 5090 before
KV allocation, and SGLang also reports rope-scaling defaults that require an
accuracy caveat. The viable GLM path for this bakeoff was the llama.cpp GGUF
`UD-Q4_K_XL` recipe, not BF16 SGLang.

## `mistralai/Devstral-Small-2-24B-Instruct-2512`

Successful evidence:

- `devstral-small2-vllm-fp8-8k.voice.json`
- `devstral-small2-vllm-fp8-8k.bakeoff.json`

The 32K vLLM FP8 profile was attempted first and exceeded practical RTX 5090 KV
headroom; the viable run used the reduced 8K recipe. The current retained logs
also capture the Mistral OpenAI-compatible request-shape incompatibility that
was fixed in `anvil_serving.voice.stages.llm`:

```text
ValueError: chat_template is not supported for Mistral tokenizers.
ValueError: reasoning_effort=low is not supported by Mistral models. Supported values are: ['none', 'high'].
```

Root cause of the compatibility failure: the Anvil Voice LLM stage was sending
Qwen-style thinking-disable knobs unconditionally. The stage now retries without
`chat_template_kwargs`, then retries with `reasoning_effort="none"` when a
Mistral-family endpoint rejects `reasoning_effort="low"`.

