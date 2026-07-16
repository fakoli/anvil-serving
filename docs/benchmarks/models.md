# RTX PRO 6000 model recipes and gotchas

This page turns the [benchmark tables](index.md) into operating guidance for one
RTX PRO 6000 Blackwell 96 GB. The durable recipes live in
[`examples/fakoli-dark/docker-compose.experiment.yml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml)
and are registered in
[`examples/fakoli-dark/serves.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.toml).
The snippets below use the managed CLI rather than reproducing long container
commands that can drift.

## Common operating sequence

Pin a checkpoint revision, start only one single-card candidate at a time, and
gate the exact endpoint before collecting benchmark evidence:

```bash
anvil-serving models pull OWNER/REPO --revision COMMIT_SHA --confirm
anvil-serving serves up SERVICE --manifest examples/fakoli-dark/serves.toml --recreate --confirm
anvil-serving eval preflight --base-url http://127.0.0.1:PORT/v1 --model SERVED_NAME --confirm
```

Pulling a commit does not by itself force `vllm serve OWNER/REPO` to select that
commit. A reproducible service must also pass the revision or reference an
immutable local snapshot. Where the current managed service does not enforce
the observed revision, its model section says so explicitly.

`models pull` first checks the named process environment variable and otherwise
reads one `--token-file`, which defaults to `~/.env`, without printing the token.
It does not scan a repository `.env` chain. Never place `HF_TOKEN` in a manifest,
Compose file, test fixture, or log.

Unless a model section says otherwise, the July 2026 Heavy experiments used FP8
KV cache, text-only mode, prefix caching disabled for independent-prompt
comparisons, and a maximum of five admitted sequences.

## Gemma 4 12B IT QAT W4A16

| Setting | Tested value |
|---|---|
| Checkpoint | [`google/gemma-4-12B-it-qat-w4a16-ct`](https://huggingface.co/google/gemma-4-12B-it-qat-w4a16-ct), revision `5d8bb23cdbff01e89d2a1a47f3b3d29b877bca76` |
| Tokenizer/template | [`google/gemma-4-12B-it`](https://huggingface.co/google/gemma-4-12B-it), revision `12ace6d648d72bd41519e140f1185f34d38c7e3d`; July 15 template SHA-256 `ae53464b…4c6d4` |
| Managed service / endpoint | `heavy` / `http://127.0.0.1:30002/v1` |
| Served name | `gemma4-12b-it-w4a16-ct` |
| Engine path | vLLM 0.25.1; compressed-tensors W4A16; FP8 KV; `gemma4` reasoning/tool parsers |
| Context / admission | 262,144 served; 240K promotion needle passed; 5 sequences |
| Router reasoning control | `chat_template_kwargs.enable_thinking=true`; 4,096 reasoning-headroom tokens in the promotion evidence gate |

**Why choose it.** It matched ThinkingCap's perfect repeated built-in quality
result and improved quality-context TTFT from 7.83/57.60/124.70 seconds to
6.96/44.61/97.33 seconds at 32K/128K/240K. The guarded promotion also passed
disabled-thinking smoke/JSON, a 240K needle, 20/20 tools, enabled-thinking
reasoning evidence, router reload, and exact model identity.

**Gotchas.** Pin the model and tokenizer revisions independently; using only a
model revision does not pin the July 15 chat template. Do not reintroduce the old
explicit repository template override. FP8 KV is part of the measured recipe.
Cold startup includes several minutes of graph compilation on this WSL2 sm_120
host. A 256-visible-token functional gate produced a semantically correct but
truncated smoke answer and failed closed; the benchmarked 512-visible-token gate
is the production setting.

Evidence: [Gemma 4 template bakeoff and promotion](../findings/2026-07-16-gemma4-chat-template-bakeoff.md).

## ThinkingCap Qwen3.6-27B FP8

| Setting | Tested value |
|---|---|
| Checkpoint | [`bottlecapai/ThinkingCap-Qwen3.6-27B-FP8`](https://huggingface.co/bottlecapai/ThinkingCap-Qwen3.6-27B-FP8), revision `e48255afd77b403446332be0f595868337b36591` |
| Managed service / endpoint | `heavy-thinkingcap-rollback` / `http://127.0.0.1:30002/v1` when selected; experiment `cand-thinkingcap-qwen36-fp8` / `http://127.0.0.1:39031/v1` |
| Served name | `thinkingcap-qwen36-27b-fp8` |
| Engine path | vLLM nightly observed as `0.23.1rc1.dev531+ga65f93fb2`; compressed-tensors FP8; production rollback disables MTP |
| Context / admission | 262,144 served; 131K preflight retained; 5 sequences |
| Recommended eval budget | 4,096 reasoning-headroom tokens for the tested MMLU-Pro slice |

**Why choose it.** It was the best tuned-quality Qwen in the repaired protocol:
9/10 stable MMLU-Pro items and 27/30 correct attempts at 4K headroom, while ARC
was already 5/5 stable at 1K.

**Gotchas.** This FP8 path required `VLLM_USE_DEEP_GEMM=0` with the tested
nightly. The current vLLM speculative-config validator rejects the pinned
checkpoint's compressed-tensors main model plus FP8 MTP head, so the validated
rollback omits MTP. At five independent sessions, TTFT rose from 1.01 s to
4.66 s. It is a quality-first rollback, not the lowest-latency choice. Preserve
the observed engine version and image digest with every rerun.

Evidence: [Qwen protocol-v2 comparison](../findings/2026-07-12-qwen36-protocol-v2-comparison.md).

## Nemotron 3 Super 120B NVFP4

| Setting | Tested value |
|---|---|
| Checkpoint | [`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4`](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4), revision `4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6` |
| Managed service / endpoint | `cand-nemotron3-super-120b` / `http://127.0.0.1:39033/v1` |
| Served name | `nemotron3-super-120b-a12b-nvfp4` |
| Engine path | vLLM nightly; official `super_v3` reasoning parser; `qwen3_coder` tool parser; `mamba_cache_dtype=float16` |
| Context / admission | 131,072 served and validated; 5 sequences |
| Recommended eval budget | 1,024 reasoning-headroom tokens |

**Why choose it.** It is the best matched-budget Heavy balance measured: 5/5
stable ARC, 8/10 stable MMLU-Pro, and a 260.91 s repeated MMLU wall time at 1K.
It also completed five sessions with 2.52 s TTFT p50.

**Gotchas.** The model card advertises up to 1M context, but this recipe served
and validated **131K**. The reported 3,999,467-token shared KV pool is not proof
that several million-token requests fit simultaneously. Doubling reasoning
headroom to 2K did not improve MMLU-Pro and added about 57 seconds. The reasoning
parser is vendored from the same pinned model revision and must remain aligned.

Evidence: [Heavy intelligence challengers](../findings/2026-07-12-heavy-intelligence-challengers.md)
and [protocol v2](../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md).

## Qwen3.6-27B community NVFP4 + MTP

| Setting | Tested value |
|---|---|
| Checkpoint | [`sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP`](https://huggingface.co/sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP), revision `6f194695406a3bc88a00573187d5b2eecf984a99` |
| Managed service / endpoint | `cand-qwen36-heavy-mtp` / `http://127.0.0.1:39027/v1` |
| Served name | `qwen36-27b-nvfp4-mtp` |
| Engine path | vLLM nightly; ModelOpt NVFP4; native MTP 3; Qwen reasoning and `qwen3_coder` tool parsers |
| Context / admission | 262,144 served and needle-validated; 5 sequences |
| Recommended eval budget | 8,192 headroom for quality calibration |

**Why choose it.** This was the throughput/capacity winner of the original
Qwen3.6 variation bake-off: 0.63 s TTFT at concurrency one, 3.22 s at five,
95.0 tok/s controlled long generation, and a validated 262K needle.

**Gotchas.** It is a community checkpoint, so its pinned revision and source
provenance matter. At a matched 1K reasoning budget it scored 0/10 stable
MMLU-Pro because completion budget was exhausted; at 8K it reached 8/10 in a
one-pass calibration with two truncations. Do not turn that calibration into a
repeatability claim. Prefix caching was disabled to keep the MTP comparison
clean.

Evidence: [Qwen variation bake-off](../findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md).

## Qwen3.6-27B official FP8

| Setting | Tested value |
|---|---|
| Checkpoint | [`Qwen/Qwen3.6-27B-FP8`](https://huggingface.co/Qwen/Qwen3.6-27B-FP8), revision `e89b16ebf1988b3d6befa7de50abc2d76f26eb09` |
| Managed service / endpoint | `cand-qwen36-fp8` / `http://127.0.0.1:39030/v1` |
| Served name | `qwen36-27b-fp8` |
| Engine path | vLLM nightly; FP8 weights; MTP 3 |
| Context / admission | 262,144 served; 131K preflight retained; 5 sequences |
| Recommended eval budget | 4,096 headroom for the tested quality calibration |

**Why choose it.** It is the official base checkpoint and passed preflight,
five-session completion, and the current built-in eval. At 4K it reached 5/5
ARC and 8/10 MMLU-Pro in one-pass calibration.

**Gotchas.** `VLLM_USE_DEEP_GEMM=0` was required with the tested nightly. It was
the slowest Qwen variant in the independent-prompt capacity test: 1.59 s TTFT at
one session and 5.68 s at five. Official provenance alone did not make it the
best local recipe.

## Unsloth Qwen3.6-27B NVFP4

| Setting | Tested value |
|---|---|
| Checkpoint | [`unsloth/Qwen3.6-27B-NVFP4`](https://huggingface.co/unsloth/Qwen3.6-27B-NVFP4), revision `ccdaab7e68af2409599b8949a8f2685703c9bae5` |
| Managed service / endpoint | `cand-unsloth-qwen36-27b-nvfp4` / `http://127.0.0.1:39036/v1` |
| Served name | `unsloth-qwen36-27b-nvfp4` |
| Engine path | vLLM 0.25.0; FlashInfer 0.6.13; CUTLASS DSL 4.5.2; native FlashInfer-CUTLASS NVFP4; embedded MTP 2 |
| Context / admission | 262,144 served; five sequences; 5-session completion validated |
| Recommended eval budget | 8,192 headroom for the tested quality calibration |

**Why choose it.** It is a current publisher-tuned Blackwell path. Full
preflight was operator-observed but its transcripts were not retained; the raw
capacity artifact does retain all five concurrent completions. Its 8K one-pass
MMLU-Pro calibration reached 9/10 with one truncation.

**Gotchas.** It requires a materially newer engine stack than the other Qwen
recipes; do not silently run it on the shared older nightly. Its 8K result was
not repeated, and it was slower than ThinkingCap's useful 4K operating point.
The initial gated pull exposed missing Hugging Face authentication, which is why
the managed pull path resolves `HF_TOKEN` from `~/.env` as well as the normal
environment chain.

Evidence: [Qwen protocol-v2 comparison](../findings/2026-07-12-qwen36-protocol-v2-comparison.md).

## Mistral Small 4 119B NVFP4

| Setting | Tested value |
|---|---|
| Checkpoint | [`mistralai/Mistral-Small-4-119B-2603-NVFP4`](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603-NVFP4), revision `d57a94c74a961e1f9b489b8b3e792923ca29149b` |
| Managed service / endpoint | `cand-mistral-small4-119b-nvfp4` / `http://127.0.0.1:39032/v1` |
| Served name | `mistral-small4-119b-a6b-nvfp4` |
| Engine path | vLLM nightly; `TRITON_MLA`; Mistral reasoning/tool parsers; text-only |
| Context / admission | 131,072 served and validated; 5 sequences |
| Recommended eval budget | 2,048 reasoning-headroom tokens |

**Why choose it.** It had the best short independent-prompt latency and batch
throughput in this round, while still reaching 5/5 stable ARC at 2K.

**Gotchas.** Qwen-style `chat_template_kwargs` fail closed with HTTP 400. The
model card specifies OpenAI `reasoning_effort`, which the harness did not expose
in the first round. Protocol v2 repaired that mismatch, but MMLU-Pro remained
5/10 stable. Its 256K advertised maximum was not validated; the recipe is 131K.

## Nemotron 3 Puzzle 75B NVFP4

| Setting | Tested value |
|---|---|
| Checkpoint | [`nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4`](https://huggingface.co/nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4), observed revision `1d370e47fbc56d1019a471c2339663cdbbb5236f` |
| Managed service / endpoint | `cand-nemotron3-puzzle-75b` / `http://127.0.0.1:39026/v1` |
| Served name | `nemotron3-puzzle-75b-nvfp4` |
| Engine path | vLLM nightly; NVFP4; MTP 3 |
| Context / admission | 131,072; 2 sequences; preflight and 20/20 tool calls passed |

**Why choose it.** It produced 137.0 tok/s in a controlled long-generation run,
a 1.50× gain over the 91.4 tok/s no-MTP control.

**Gotchas.** Its later conventional short run generated only 101 tokens across
ten requests, so the 15.22 aggregate tok/s number is not a useful decode result.
The old deterministic planning score is protocol-invalid because hidden
reasoning exhausted the visible-answer budget. Capacity evidence remains useful;
quality must be rerun under protocol v2 before promotion. The managed Compose
service does not currently pass `--revision` and uses a mutable nightly image,
so the observed checkpoint and engine must be pinned before claiming a rerun is
exactly reproducible.

## GPT-OSS-120B

| Setting | Tested value |
|---|---|
| Checkpoint | [`openai/gpt-oss-120b`](https://huggingface.co/openai/gpt-oss-120b), observed revision `b5c939de8f754692c1647ca79fbf85e8c1e70f8a` |
| Managed service / endpoint | `heavy-gptoss-rollback` / `http://127.0.0.1:30002/v1` when explicitly selected |
| Served name | `gpt-oss-120b` |
| Engine path | vLLM nightly; native MXFP4 weights; FP8 KV; CUDA graphs enabled; OpenAI tool parser |
| Context / admission | 131,072 served and validated; engine-default sequence cap was not retained as benchmark evidence |

The former production Heavy control passed functional preflight at 131K and 20/20 tool
calls. Its established controlled long-generation result is 183.2 tok/s. Use the
model's `reasoning_effort` control rather than Qwen's `enable_thinking` field.

**Gotcha.** The old 0/5 deterministic planning result is invalid for cross-model
quality comparison: four cases returned no visible answer after hidden reasoning
consumed the completion budget. A valid comparison-grade protocol-v3 quality rerun is still
needed. The 29.87 aggregate tok/s short run is an operational batch number, not
a contradiction of the controlled decode result. The production Compose service
uses the repository name and a mutable nightly image without enforcing the
observed checkpoint revision, so preserve fresh identity and engine evidence on
every rerun until that deployment is pinned.

Evidence: [GPT-OSS deterministic recheck](../findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md).

## Laguna XS 2.1 NVFP4: rejected on sm_120

Both tested engines loaded enough of the model to appear promising, but neither
produced trustworthy output on the RTX PRO 6000:

- vLLM with FP8 KV returned corrupted text and 0/20 tool calls.
- Disabling FP8 KV changed the attention backend but stalled during retained
  cache profiling.
- Poolside's SGLang image initialized `SWARadixCache`, yet default and explicit
  chat templates still produced repetitive or off-topic output; the 131K needle
  was empty and tool fan-out was 0/20.

RadixAttention improves cache reuse and scheduling; it cannot repair an
incorrect kernel, quantization path, or chat-template execution. Do not use this
checkpoint on sm_120 until a current, hardware-matched recipe passes independent
preflight. See the [failure evidence](../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md#laguna-xs-21-nvfp4-engine-ab).

## Historical or incomplete candidates

| Candidate | What was learned | Current use |
|---|---|---|
| Qwen3.5-122B-A10B MXFP4 | Loaded through sm_120 Marlin fallback; 30.57 aggregate tok/s and 720.79 ms TTFT | Functional evidence only; old quality suite invalid |
| MiniMax-M2.5 NVFP4 | Strong external prior, but local preflight stalled after loading | Recipe lead, not a result |
| Ornith-Nano-31B-A3B NVFP4 | Loaded; context/tool behavior did not pass the gate | Rejected recipe |
| Gemma-4 / other experimental quants | Several engine or kernel compatibility failures | Keep in dated archive; do not generalize across engines |

The [chronological archive](../BENCHMARKS.md) preserves these attempts and their
raw evidence without putting incomplete candidates into the decision table.
