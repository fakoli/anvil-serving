# Publication summary: Qwen3.8 27B on dual RTX PRO 6000

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- Local direct-streaming benchmark on two equal RTX PRO 6000 Blackwell Max-Q
  GPUs under Docker Desktop/WSL2; PCIe PXB, no NVLink.
- Exact SGLang finalist: Inferact Qwen3.8-27B NVFP4 plus incoai DFlash2,
  K12, 1,024-token chunks, TP1, C8, 262,144 context, FP8 KV, BF16 Mamba state.
- Headline workload: 100 unique-canary 4K requests, 256 requested words,
  512-token ceiling, C8 per replica, thinking disabled.
- One TP1 measured 764.3 aggregate output tok/s; TP2 measured 587.9 and failed
  strict JSON twice; two independent TP1 replicas measured 1,401.8–1,423.4 tok/s with
  100/100 canaries.
- RadixArk K8 measured 746.7 tok/s with 46.9% lower median TTFT than the
  Inferact finalist but lower decode and 6.8% higher median E2E.
- kelnei/vLLM 0.27.1 MTP2 measured 503.4 tok/s versus 315.2 no-spec (+59.7%),
  with active MTP counters, but did not beat SGLang.
- Decision: DP2 wins this bounded aggregate-throughput comparison; TP2 is
  rejected; no promotion.

## Managed reproduction paths

- SGLang optimization and TP2:
  `configs/qwen38-27b-inferact-nvfp4-sglang-pro6000-optimization-recipes.toml`
- DP2 replica B:
  `configs/qwen38-27b-inferact-nvfp4-sglang-pro6000-dp2-replica-b-recipe.toml`
- RadixArk:
  `configs/qwen38-27b-radixark-nvfp4-sglang-pro6000-c8-dflash2-recipe.toml`
- kelnei/vLLM MTP2 and no-spec:
  `configs/qwen38-27b-kelnei-nvfp4-vllm0271-pro6000-mtp2-recipe.toml` and
  `configs/qwen38-27b-kelnei-nvfp4-vllm0271-pro6000-nospec-recipe.toml`

## Preferred short-post copy

Qwen3.8 27B, 2x RTX PRO 6000: DP2 ~1,402–1,423 aggregate tok/s (timing bound); TP2 failed JSON. No promotion. Evidence: https://fakoli.github.io/anvil-serving/findings/2026-09-04-qwen38-27b-pro6000-possibility-plan/

Use the preferred variant only after confirming the rendered URL keeps the
literal post at or below the platform limit.

## Reddit title

Qwen3.8-27B on 2x RTX PRO 6000: DP2 1,402–1,423 aggregate tok/s timing bound; TP2 failed JSON

## Reddit body

I translated the Helix tuning leads into a local, matched campaign rather than
trying to reproduce one headline. On two equal RTX PRO 6000 Blackwell Max-Q
cards under WSL2, the selected SGLang Inferact NVFP4 + DFlash2 recipe used K12,
1K prefill chunks, FP8 KV, BF16 Mamba state, and C8 per service.

For 100 unique-canary 4K requests with sustained 512-token output, one TP1
service measured 764.3 aggregate output tok/s. One TP2 service measured 587.9
tok/s and repeated a strict-JSON corruption. Two independently addressed TP1
replicas measured 1,401.8–1,423.4 tok/s at aggregate C16 with 100/100 canaries.

RadixArk K8 traded 46.9% lower median TTFT for lower decode and slightly higher
median E2E. kelnei/vLLM 0.27.1 MTP2 beat its exact no-spec control by 59.7%
but remained behind SGLang. These are local workload-specific results, not a
universal model ranking. Broad quality, routing/load-balancing, clients,
multimodal, and power/energy remain open. No promotion occurred.

## Screenshot alt text

Benchmark tables comparing Qwen3.8-27B configurations on two RTX PRO 6000
GPUs. Two independent SGLang TP1 replicas lead aggregate output throughput at
1,401.8–1,423.4 tokens per second; one TP1 reaches 764.3; TP2 reaches 587.9 and is
rejected after repeatable structured-JSON corruption. Additional rows show the
RadixArk TTFT tradeoff and kelnei/vLLM MTP2 versus no-spec.

## Claim ledger

| Claim | Evidence |
|---|---|
| DP2 1,401.8–1,423.4 tok/s, 100/100 canaries | [`dp2-combined-4k-c16-canary-long256-n100.json`](dp2-combined-4k-c16-canary-long256-n100.json) |
| TP1 764.3 tok/s and full latency percentiles | [`finalist-k12-chunk1k-4k-c8-canary-long256-n100.json`](finalist-k12-chunk1k-4k-c8-canary-long256-n100.json) |
| TP2 587.9 tok/s | [`opt-tp2-k12-chunk1k-4k-c8-canary-long256-n100.json`](opt-tp2-k12-chunk1k-4k-c8-canary-long256-n100.json) |
| TP2 strict JSON failed twice | [full preflight](opt-tp2-k12-chunk1k-preflight.json) and [isolated repeat](opt-tp2-k12-chunk1k-json-isolated.json) |
| RadixArk tradeoff | [`radixark-dflash-k8-4k-c8-canary-long256-n100.json`](radixark-dflash-k8-4k-c8-canary-long256-n100.json) |
| kelnei MTP2 503.4 vs no-spec 315.2 tok/s | [MTP2](kelnei-vllm0271-mtp2-4k-c8-canary-long256-n100.json) and [no-spec](kelnei-vllm0271-nospec-4k-c8-canary-long256-n100.json) |
| MTP active; 93.9% draft-token acceptance | [`kelnei-vllm0271-mtp2-runtime-metrics.json`](kelnei-vllm0271-mtp2-runtime-metrics.json) |
| No promotion and exact restoration | [`restoration.json`](restoration.json) |

Platform copy and screenshots are derivative. The raw artifacts, not this
copy, define the result and never authorize promotion.
