# Model Settings — Qwen3.5-35B-A3B (AWQ 4-bit) — preferred config & process

Source: the model card (`cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit`, base `Qwen/Qwen3.5-35B-A3B`) + local `generation_config.json`. This is the live model on the 96 GB card (`served-model-name: qwen35-awq-local`).

## What this model actually is (not just a stopgap)
35B total / **3B active**, 40 layers, hybrid **Gated DeltaNet (linear attn) + Gated Attention + MoE** (256 experts, 8+1 active), multimodal, **262K native context (→1M via YaRN)**, Apache-2.0. Benchmarks: **SWE-bench Verified 69.2, Terminal-Bench 2 40.5, BFCL-V4 67.3, TAU2-Bench 81.2, LiveCodeBench 74.6.** Genuinely strong for the specialist tier.

## The gotcha: thinking is ON by default
It emits `<think>…</think>` before the answer. With a small `max_tokens` it spends the whole budget thinking and returns **empty content** (what we saw). Two correct ways to use it:
- **Non-thinking (recommended for bulk execution packets):** send `chat_template_kwargs: {"enable_thinking": false}` → direct answers.
- **Thinking (for hard review/planning):** leave thinking on but give **adequate `max_tokens` (≥4096)** so it finishes thinking AND answers. (It does NOT support `/think` `/nothink` soft switches.)
- **Multi-turn:** history should contain only final answers, not prior `<think>` content (the chat template handles this; OpenClaw must too if it builds prompts itself).

## Recommended sampling (verbatim from the card)
| Mode | temperature | top_p | top_k | min_p | presence_penalty | repetition_penalty |
|---|---|---|---|---|---|---|
| Thinking — general | 1.0 | 0.95 | 20 | 0.0 | 1.5 | 1.0 |
| Thinking — precise coding (WebDev) | 0.6 | 0.95 | 20 | 0.0 | 0.0 | 1.0 |
| **Instruct / non-thinking — general** | **0.7** | **0.8** | **20** | **0.0** | **1.5** | **1.0** |
| Instruct / non-thinking — reasoning | 1.0 | 1.0 | 40 | 0.0 | 2.0 | 1.0 |

Output length: card recommends ~**32,768 tokens** for most queries (give it room). Context: keep **≥128K** to preserve thinking quality (we serve at 128K).

## OpenClaw request snippet (specialist/non-thinking coding tier)
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
(For OpenClaw model config, set these as the per-agent default `generate_cfg`/`extra_body` for the local specialist.)

## Server flags applied (in docker-compose.yml)
- `--reasoning-parser qwen3` + `--tool-call-parser qwen3_coder` — parse thinking + Qwen tool calls (needed for agentic use).
- `--language-only` — skip the vision encoder (we serve text/code) → frees memory for KV, faster load.
- `--context-length 131072` (128K), `--kv-cache-dtype fp8_e5m2`, `--mem-fraction-static 0.88`, `--max-running-requests 16`, `--weight-loader-disable-mmap` (avoids the virtiofs mmap slowness), `--cuda-graph-max-bs-decode 8`.

### Optional: faster decode via the model's native MTP (self-speculative)
The model ships a Multi-Token-Prediction head. To try the throughput boost (helps the ~21 tok/s decode) add:
`--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4`
Benchmark before/after — it adds startup time and may not help at high concurrency.

## Process to change model settings (repeatable)
1. Edit `deploy/docker-compose.yml` (flags) and/or this file (sampling).
2. Apply: `powershell -File deploy\sglang-up.ps1` (recreates the container).
3. Watch: `docker logs -f sglang` → "ready to roll"; `curl http://localhost:30000/health` → 200.
4. Validate: `scripts\preflight.py` / `benchmark.py` — pass `--enable-thinking false`-equivalent params (these scripts need the `chat_template_kwargs` flag for this model; add it before relying on them).
