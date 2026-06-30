# Model Settings — Qwen3.5-35B-A3B (AWQ 4-bit)

Source: the model card (`cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit`, base `Qwen/Qwen3.5-35B-A3B`) + the
model's `generation_config.json`. This document covers serving configuration for this model on the
fast/heavy local tier (example: `served-model-name: qwen35-awq-local`).

## What this model is

35B total / **3B active**, 40 layers, hybrid **Gated DeltaNet (linear attn) + Gated Attention +
MoE** (256 experts, 8+1 active), multimodal, **262K native context (→1M via YaRN)**, Apache-2.0.
Benchmarks: **SWE-bench Verified 69.2, Terminal-Bench 2 40.5, BFCL-V4 67.3, TAU2-Bench 81.2,
LiveCodeBench 74.6.** Genuinely strong for the specialist tier.

## The gotcha: thinking is ON by default

The model emits `<think>…</think>` before the answer. With a small `max_tokens` budget it spends
the entire budget reasoning and returns **empty content** — a valid-looking JSON response with an
empty `content` array. Two correct ways to use the model:

- **Non-thinking (recommended for bulk execution packets):** send
  `chat_template_kwargs: {"enable_thinking": false}` → direct answers, no thinking overhead.
- **Thinking (for hard review/planning):** leave thinking on but give **adequate `max_tokens`
  (≥4096)** so the model finishes reasoning and still answers. (Does NOT support `/think` `/nothink`
  soft switches.)
- **Multi-turn:** conversation history should contain only final answers, not prior `<think>` content
  (the chat template handles this; any harness that builds prompts itself must do the same).

## Recommended sampling (verbatim from the model card)

| Mode | temperature | top_p | top_k | min_p | presence_penalty | repetition_penalty |
|---|---|---|---|---|---|---|
| Thinking — general | 1.0 | 0.95 | 20 | 0.0 | 1.5 | 1.0 |
| Thinking — precise coding (WebDev) | 0.6 | 0.95 | 20 | 0.0 | 0.0 | 1.0 |
| **Instruct / non-thinking — general** | **0.7** | **0.8** | **20** | **0.0** | **1.5** | **1.0** |
| Instruct / non-thinking — reasoning | 1.0 | 1.0 | 40 | 0.0 | 2.0 | 1.0 |

Output length: the card recommends ~**32,768 tokens** for most queries (give it room). Context:
keep **≥128K** to preserve thinking quality.

## Request snippet (non-thinking coding tier)

```jsonc
{
  "model": "qwen35-awq-local",
  "messages": [ /* lean scoped prompt */ ],
  "max_tokens": 4096,
  "temperature": 0.7, "top_p": 0.8, "presence_penalty": 1.5,
  "extra_body": {
    "top_k": 20, "min_p": 0.0,
    "chat_template_kwargs": { "enable_thinking": false }
  }
}
```

For OpenClaw or any harness that supports per-model default params, set these as the per-agent
`generate_cfg`/`extra_body` for the local specialist slot.

## SGLang server flags

- `--reasoning-parser qwen3` + `--tool-call-parser qwen3_coder` — parse thinking tokens + Qwen
  tool calls (required for agentic use).
- `--language-only` — skip the vision encoder (text/code only) → frees VRAM for KV, faster load.
- `--context-length 131072` (128K), `--kv-cache-dtype fp8_e5m2`, `--mem-fraction-static 0.88`,
  `--max-running-requests 16`, `--weight-loader-disable-mmap` (avoids the virtiofs mmap slowness on
  Windows bind mounts), `--cuda-graph-max-bs-decode 8`.

### Optional: faster decode via the model's native MTP (self-speculative)

The model ships a Multi-Token-Prediction head. To try the throughput boost add:

```
--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4
```

Benchmark before/after — it adds startup time and may not help at high concurrency.

## Process to change model settings (repeatable)

1. Edit the `docker-compose.yml` server flags and/or the sampling params above.
2. Recreate the container with the updated compose file (`docker compose up -d --force-recreate`).
3. Watch: `docker logs -f sglang` → "ready to roll"; `curl http://127.0.0.1:30000/health` → 200.
4. Validate: `anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model qwen35-awq-local`
   — pass `--no-thinking` (injects `chat_template_kwargs:{enable_thinking:false}`) so the model
   returns actual content rather than timing out on the thinking budget.
