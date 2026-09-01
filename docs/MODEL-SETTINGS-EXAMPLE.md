# Historical model settings — Qwen3.5-35B-A3B (AWQ 4-bit)

!!! note "Historical configuration note"

    This page preserves a July 2026 model-settings example. It is not a current
    deployment recommendation or an active operator configuration. Start with
    [Model lifecycle](MODEL-LIFECYCLE.md) for managed recipes and the
    [model dossiers](benchmarks/models/index.md) for current evidence status.

Source at the time of the note: the unpinned model card
(`cyankiwi/Qwen3.5-35B-A3B-AWQ-4bit`, base `Qwen/Qwen3.5-35B-A3B`) and its
`generation_config.json`. The example used the served name `qwen35-awq-local`.

## What this model is

35B total / **3B active**, 40 layers, hybrid **Gated DeltaNet (linear attn) + Gated Attention +
MoE** (256 experts, 8+1 active), multimodal, **262K native context (→1M via YaRN)**, Apache-2.0.
Benchmarks: **SWE-bench Verified 69.2, Terminal-Bench 2 40.5, BFCL-V4 67.3, TAU2-Bench 81.2,
LiveCodeBench 74.6.** These were external model-card claims, not local
qualification results.

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

## Historical SGLang server flags

- `--reasoning-parser qwen3` + `--tool-call-parser qwen3_coder` — parse thinking tokens + Qwen
  tool calls (required for agentic use).
- `--language-only` — skip the vision encoder (text/code only) → frees VRAM for KV, faster load.
- `--context-length 131072` (128K), `--kv-cache-dtype fp8_e5m2`, `--mem-fraction-static 0.88`,
  `--max-running-requests 16`, `--cuda-graph-max-bs-decode 8`. (`--weight-loader-disable-mmap` was
  dropped in #108 — it was a 9P-bind-mount-era workaround, obsolete now that weights load from a
  named Docker volume.)

### Historical native-MTP experiment

The model ships a Multi-Token-Prediction head; SGLang self-speculates from it directly (no
separate draft model or added VRAM cost):

```
--speculative-algorithm NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4
```

This setting was enabled in a July 2026 Compose configuration after a local
experiment reported higher decode throughput and a TTFT tradeoff under
concurrency. It is not the current default and does not generalize to another
checkpoint, engine, runtime build, GPU, or context. Historical rationale:
[ADR-0008](adr/0008-heavy-tier-speculative-decoding.md).

## Apply settings through the managed lifecycle

Do not copy the historical flags directly into a running container. Record a
pinned candidate in the serve-recipe registry, inspect the resolved recipe,
preview its load, and then confirm only the reviewed plan:

```bash
anvil-serving models recipes show <recipe-id>
anvil-serving models recipes load <recipe-id> --dry-run
anvil-serving models recipes load <recipe-id> --confirm
anvil-serving models recipes status <recipe-id>
```

Run preflight as a separate, explicit qualification gate:

```bash
anvil-serving eval preflight \
  --base-url http://127.0.0.1:<port>/v1 \
  --model <served-model-name> \
  --dry-run

anvil-serving eval preflight \
  --base-url http://127.0.0.1:<port>/v1 \
  --model <served-model-name> \
  --confirm
```

Qualification evidence does not promote or expose a route. Follow
[model promotion and rollback](MODEL-PROMOTION.md) for that separate gate.
