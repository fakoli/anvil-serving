# Qwen3.5-122B-A10B-NVFP4 heavy-tier candidate — fakoli-dark

**Point-in-time record, 2026-07-10.** Adds `nvidia/Qwen3.5-122B-A10B-NVFP4` as an anvil-serving-managed
**heavy-tier evaluation candidate**, alongside (not replacing) the GPT-OSS-120B production heavy tier.
This is a candidate record, not a promotion: the router was not modified and no `heavy-local` change was made.

## Tested configuration

| Field | Value |
|---|---|
| Tested model | `nvidia/Qwen3.5-122B-A10B-NVFP4` (rev `98915d837c4e7c87ac8296d02e89de19b3207e6d`) |
| Served model name | `qwen35-122b-a10b-nvfp4` |
| Params | 122B total / ~10B active (MoE, `qwen3_5_moe`) |
| Host / topology | fakoli-dark, Windows 11 + Docker Desktop (WSL2 backend); loopback `:39017` only |
| GPU | RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 (`GPU-d0f446cf-1771-414c-e116-a39138798a8c`), pinned single-GPU (no tensor parallelism) |
| Engine / image | vLLM `0.19.0+6bc3197f.nv26.04` — `nvcr.io/nvidia/vllm:26.04-py3` (`sha256:98494fda…b2cb75b27`) |
| Quantization | `modelopt_fp4` (NVFP4 weights), FP8 KV cache |
| Context / concurrency | 131072 max-model-len, `max-num-seqs 1` |
| Managed serve | `heavy-qwen35-122b` (serves.toml / docker-compose.experiment.yml) |

## Gate outcomes

Correctness gates (`preflight`) were run before the capacity/bakeoff capture.

| Gate | Result |
|---|---|
| Preflight — short coding | PASS |
| Preflight — structured JSON | PASS |
| Preflight — needle @ ~128k ctx | PASS (sentinel retrieved) |
| Preflight — 20 shared-prefix tool calls | PASS (20/20 clean) |
| Bakeoff — intelligence pass rate | **1.0** |
| Bakeoff — session recall | PASS |
| Bakeoff — tool call | PASS |
| Bakeoff — usable context | **131072 tokens** |
| Reasoning parser (`enable_thinking:true`) | PASS — reasoning isolated in `reasoning` field, no `<think>` leak into content, final answer distinct |
| Failures | none |

## Metrics

| Metric | Value | Notes |
|---|---|---|
| Warm throughput (single-stream) | **38.8 tok/s** | 10 req × 8192-ctx |
| TTFT p50 (short) | 223 ms | 8192-ctx bench |
| Long-context (100k) TTFT p50 | ~28 s | 3 req × 100000-ctx; 8.06 tok/s (prefill-dominated) |
| Bakeoff p50 TTFT / e2e | 31.9 s / 32.8 s | **context-probe-dominated** (131k prefill), not representative of short-request latency |
| Model weights (VRAM) | 72.35 GiB | |
| Total VRAM reserved | 94.1 GiB | `gpu-memory-utilization 0.95` on 96 GB |
| FP8 KV cache | 354,960 tokens → **8.97× concurrency @ 131072** | |
| Cold start | ~4m10s (cold) / ~2m15s (warm cache) | weights 76.8s + torch.compile 71s + engine init 138s |
| CUDA graphs | captured (PIECEWISE=2, FULL=1) | no `--enforce-eager` needed |
| Host (WSL) RAM peak / swap | 6.0 GB / ~0 | no OOM |
| RTX 5090 (fast tier) | untouched throughout (2.6 GB baseline) | |

## Comparison

The current production heavy tier, `openai/gpt-oss-120b`, measures **183.2 tok/s** (see [BENCHMARKS.md](../BENCHMARKS.md)).
This candidate is ~4.7× slower single-stream (38.8 tok/s) but delivers a full **131072-token** context window with
8.97× concurrency, verified 128k needle retrieval, 100% intelligence pass, clean tool-calling, and separated reasoning.
It reads as a **quality / long-context** tier, **not** a latency-sensitive fast/chat tier.

## Caveats

- **FP8 KV uncalibrated scale.** The checkpoint ships no q/k scale factors, so vLLM uses `scale=1.0`. This is a
  possible accuracy note for FP8 attention on quality-sensitive routing; a calibrated run should precede any promotion.
- **Bakeoff latency p50 is context-probe-dominated.** The 31.9 s TTFT reflects the 131k-token context suite, not
  typical short-request latency (223 ms at 8k).
- Recorded thinking mode was `disabled` for the correctness/intelligence gates (matching preflight, to avoid the
  thinking-budget-exhaustion false-fail). Reasoning quality with thinking enabled was validated separately (clean parse).

## Evidence

- Bakeoff artifact: [`heavy-tier-bakeoff-evidence/qwen35-122b-a10b-vllm-nvfp4-131k.bakeoff.json`](heavy-tier-bakeoff-evidence/qwen35-122b-a10b-vllm-nvfp4-131k.bakeoff.json) (`anvil-serving.fast-tier-bakeoff/v1`, run `fast-bakeoff-20260711T002601Z`)
- Recipe: `configs/serve-recipes.toml` → `nvidia/Qwen3.5-122B-A10B-NVFP4` (`status = "verified"`)

## Reproduce

```bash
# 1. Pull weights into the ext4 docker volume (never a C:/ bind mount)
anvil-serving models pull nvidia/Qwen3.5-122B-A10B-NVFP4 --volume vllm-hfcache --token-env HF_TOKEN

# 2. Serve on the RTX PRO 6000 (loopback :39017), managed via anvil-serving serves
anvil-serving serves up --manifest examples/fakoli-dark/serves.toml heavy-qwen35-122b
#    NOTE: the NGC vllm:26.04 image rejects a UUID-form CUDA_VISIBLE_DEVICES (int() parse);
#    the compose service pins the PRO 6000 by PCI_BUS_ID index (=1) instead.

# 3. Correctness gate
anvil-serving preflight --base-url http://127.0.0.1:39017/v1 --model qwen35-122b-a10b-nvfp4 \
  --needle-ctx 128000 --tool-batch 20 --no-thinking

# 4. Bakeoff capture (evidence JSON + append-only notebook row)
anvil-serving eval benchmark run --bakeoff \
  --base-url http://127.0.0.1:39017/v1 --model qwen35-122b-a10b-nvfp4 \
  --candidate-id qwen35-122b-a10b --config-id vllm-nvfp4-131k \
  --context-targets 131072 --suite chat,context,tool,session,intelligence --thinking-mode disabled \
  --notebook .anvil/benchmarks.sqlite --notebook-task heavy-tier --notebook-hardware rtx6kpro \
  --evidence-out docs/findings/heavy-tier-bakeoff-evidence/qwen35-122b-a10b-vllm-nvfp4-131k.bakeoff.json
```

> Operator note (2026-07-10): the serve start/stop steps in this run used `docker compose` directly rather than
> `anvil-serving serves up/down`, because a `--confirm` catch-22 on the `codex/operator-cli-v2-beta` CLI branch made
> the guarded serve-lifecycle commands unusable non-interactively (tracked in issue #191). Use `anvil-serving serves`
> once that is fixed.

## Recommendation

**Retain GPT-OSS-120B as the primary heavy tier; keep this as a verified candidate and gather more evidence before promotion.**
A quality comparison (planning-eval / shadow-eval) against gpt-oss-120b — not just throughput — and a calibrated-FP8-KV
run should precede any `heavy-local` promotion. Promotion remains human-gated.
