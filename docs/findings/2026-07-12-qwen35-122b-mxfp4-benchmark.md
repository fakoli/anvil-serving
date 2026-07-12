# Qwen3.5-122B-A10B MXFP4 single-card benchmark

**Point-in-time record, 2026-07-12.** This run tested the cached
`olka-fi/Qwen3.5-122B-A10B-MXFP4` checkpoint on Fakoli Dark's single RTX PRO
6000. It was prompted by community reports of 75–90 tok/s from Qwen3.5-122B
MXFP4 variants on the same GPU class. The result verifies a reproducible
Anvil-managed serve but does **not** support Heavy-tier promotion.

## Configuration

| Field | Tested value |
|---|---|
| Served model | `qwen35-122b-mxfp4` |
| Checkpoint revision | `345839ea666a70f5035672f7c88afcba6281921f` |
| Host | Fakoli Dark, Windows 11, Docker Desktop/WSL2 |
| GPU | RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 |
| Engine | vLLM `0.23.1rc1.dev531+ga65f93fb2` |
| Image | `vllm/vllm-openai@sha256:907377dd…5319ff3e` |
| Quantization | compressed-tensors MXFP4, forced Marlin W4A16 fallback |
| KV cache | FP8 |
| Context / sequences | 131,072 / 2 |
| Endpoint | loopback `127.0.0.1:30004` |
| Managed serve | `heavy-qwen35-122b-mxfp4` |
| Thinking | disabled for preflight and both requested benchmarks |

The engine confirmed `MarlinMxFp4LinearKernel` and `MarlinExperts`. Weight
loading took 107.63 seconds; full model initialization reported 69.21 GiB for
the loaded model and the post-benchmark GPU reading was 89,336 MiB used.
During this isolated window the RTX 5090 remained model-free, the Anvil 0.12.0
router was idle on `127.0.0.1:8000`, and the dashboard collected telemetry
behind Tailscale Serve.

## External advisory priors

These sources selected the experiment; they are not local promotion evidence.

| Source | Observed date | Age class on 2026-07-12 | Evidence | Relevance |
|---|---|---|---|---|
| [Single RTX PRO 6000 MXFP4_MOE llama.cpp report](https://www.reddit.com/r/LocalLLaMA/comments/1roiyvo/rtx6k_server_450w_qwen35122ba10b_mxfp4_moe/) | 2026-03-08 | stale (>120 days) | community benchmark | ~80 tok/s empty and ~77 tok/s at 8K, but different GGUF weights and engine |
| [Single-card olka-fi MXFP4 configuration](https://www.reddit.com/r/LocalLLM/comments/1re8si5/qwen35122ba10b_vs_old_codernext80b_both_at_nvfp4/) | observed 2026-03 | stale | community recipe | ~90 tok/s claimed through vLLM; closest checkpoint/config lead |
| [Qwen3.5-122B model card](https://huggingface.co/Qwen/Qwen3.5-122B-A10B) | observed 2026-07-12 | current | official model facts | family identity and intended behavior; no RTX PRO 6000 speed claim |

The llama.cpp report is not apples-to-apples with this run. It used
`MXFP4_MOE` GGUF weights and llama.cpp's kernels; this run used the olka-fi
compressed-tensors checkpoint through vLLM's W4A16 Marlin fallback.

## Correctness gate

Preflight ran before capacity or eval work:

```powershell
anvil-serving eval preflight --base-url http://127.0.0.1:30004/v1 `
  --model qwen35-122b-mxfp4 --needle-ctx 128000 --tool-batch 20 `
  --no-thinking --confirm
```

All checks passed: short coding in 2.4 seconds, structured JSON, the 128K
needle in 25.8 seconds, and 20/20 shared-prefix tool calls.

## Standard throughput benchmark

The standard Anvil benchmark used 10 sequential requests, 8,192 context
tokens, a 256-token cap, and disabled thinking. Raw artifact:
[standard-throughput.json](2026-07-12-qwen35-122b-mxfp4-evidence/standard-throughput.json).

| Metric | Result |
|---|---:|
| Completion | 10/10 |
| Aggregate output throughput | **30.57 tok/s** |
| TTFT p50 / p95 | 720.79 / 974.40 ms |
| E2E p50 / p95 | 1066.43 / 1327.91 ms |
| Output tokens | 345 |

This is 21% below the prior local Qwen3.5-122B NVFP4 result of 38.8 tok/s and
far below the stale llama.cpp MXFP4_MOE community report. The local result is
evidence that changing only to this vLLM/Marlin MXFP4 path does not deliver the
expected speedup.

## Deterministic session-eval benchmark

The new `--suite-file` path ran the session-derived planning suite captured in
[planning-milestone-execution.suite.json](2026-07-12-qwen35-122b-mxfp4-evidence/planning-milestone-execution.suite.json).
Raw result:
[deterministic-planning-eval.json](2026-07-12-qwen35-122b-mxfp4-evidence/deterministic-planning-eval.json).

| Eval | Result | Failed deterministic checks |
|---|---|---|
| Low-overhead dashboard architecture | fail | stdlib, bounded retention, raw/outside-git boundary, degraded capability |
| Milestone dependency order | fail | exact ordered chain |
| Proof-buffer recovery | fail | reject, capture-evidence, resubmit/strict |
| Resumable CI monitor | fail | explicit existing pull-request reuse |
| Local/remote main reconciliation | **pass** | — |

Overall: **1/5 passed**. Some answers were directionally reasonable, but the
suite deliberately checks operational contract language. The failures therefore
remain failures rather than being waived through subjective review.

## Router and recommendation

The benchmark-only router advertised the normal intent vocabulary, but its
quality gate refused a `planning` request for this new fingerprint with
`no quality-gated tier is available`. That is the correct fail-closed outcome.
No profile, production compose service, OpenClaw configuration, or live routing
trust decision changed.

**Recommendation: do not promote this MXFP4/Marlin configuration.** Retain it as
a reproducible engine/weight A/B. If Qwen3.5-122B speed investigation continues,
the next materially distinct test should use the actual `MXFP4_MOE` GGUF through
a pinned recent llama.cpp build; repeating this Marlin path is unlikely to close
the observed gap.
