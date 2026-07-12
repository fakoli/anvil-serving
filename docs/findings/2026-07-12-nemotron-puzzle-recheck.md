# Nemotron Puzzle 75B Heavy-candidate recheck

**Point-in-time record, 2026-07-12.** This run reloaded
`nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4` on Fakoli Dark's
single RTX PRO 6000, repeated the normal Anvil correctness and throughput
workflow, and ran the new externally-authored deterministic planning suite.
The endpoint was healthy and fast to first token, but the new suite did not
show better planning-contract behavior than the Qwen3.5-122B MXFP4 control.
No production tier or router policy changed.

## Configuration

| Field | Tested value |
|---|---|
| Served model | `nemotron3-puzzle-75b-nvfp4` |
| Checkpoint revision | `1d370e47fbc56d1019a471c2339663cdbbb5236f` |
| Host | Fakoli Dark, Windows 11, Docker Desktop/WSL2 |
| GPU | RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 |
| Engine | vLLM `0.23.1rc1.dev531+ga65f93fb2` |
| Image | `vllm/vllm-openai@sha256:907377dd...5319ff3e` |
| Quantization | NVIDIA NVFP4 MoE |
| Speculative decode | MTP, 3 speculative tokens |
| KV cache | FP8 |
| Context / sequences | 131,072 / 2 |
| Endpoint | loopback `127.0.0.1:39026` |
| Managed serve | `cand-nemotron3-puzzle-75b` |
| Thinking | disabled for preflight and both requested benchmarks |

The cold managed start took about 10 minutes. Weight loading accounted for
291.75 seconds and loaded 49.83 GiB; engine initialization then compiled and
warmed Mamba/MTP kernels. Steady GPU memory was approximately 90,588 MiB.
vLLM selected the FlashInfer CUTLASS NVFP4 MoE path. It also warned that the
FP8 attention q/prob scale was uncalibrated and defaulted to 1.0, so accuracy
conclusions remain bounded to the checks actually run.

## Correctness gate

The direct-endpoint preflight ran before both benchmarks:

```powershell
anvil-serving eval preflight --base-url http://127.0.0.1:39026/v1 `
  --model nemotron3-puzzle-75b-nvfp4 --needle-ctx 128000 `
  --tool-batch 20 --no-thinking --confirm
```

All checks passed: short coding, structured JSON, the 128K needle, and 20/20
shared-prefix tool calls. The 128K needle completed in 13.2 seconds.

## Standard throughput benchmark

The conventional benchmark used 10 sequential requests, 8,192 context
tokens, a 256-token cap, and disabled thinking. Raw artifact:
[standard-throughput.json](2026-07-12-nemotron-puzzle-recheck-evidence/standard-throughput.json).

| Metric | Result |
|---|---:|
| Completion | 10/10 |
| Aggregate output throughput | **15.22 tok/s** |
| TTFT p50 / p95 | **458.93 / 492.91 ms** |
| E2E p50 / p95 | 661.89 / 715.97 ms |
| Output tokens | 101 |

This short mixed-prompt workload is not a clean decode-rate measurement for
Nemotron: the model ended the ten answers after only 101 total output tokens.
It does show substantially lower loaded-endpoint TTFT than the Qwen3.5-122B
MXFP4 run (458.93 versus 720.79 ms). The prior controlled long-generation MTP
A/B remains the meaningful decode measurement: 137.0 tok/s with MTP n=3.

## Deterministic session-eval benchmark

The same session-derived planning suite used for the Qwen control ran through
the new `--suite-file` path. Raw result:
[deterministic-planning-eval.json](2026-07-12-nemotron-puzzle-recheck-evidence/deterministic-planning-eval.json).

| Eval | Result | Failed deterministic checks |
|---|---|---|
| Low-overhead dashboard architecture | fail | stdlib, bounded retention, raw/outside-git boundary, degraded capability |
| Milestone dependency order | fail | exact ordered chain |
| Proof-buffer recovery | fail | reject, capture-evidence, resubmit/strict |
| Resumable CI monitor | fail | explicit timeout alignment |
| Local/remote main reconciliation | fail | explicit backup step |

Overall: **0/5 passed**, compared with Qwen3.5-122B MXFP4's 1/5 on the same
suite. Several answers were directionally sensible and four cases satisfied
some of their required clauses, but deterministic contract checks are
intentionally not waived through subjective review. This result therefore
does not establish that Nemotron behaves better for these planning workflows.

## Recommendation

Nemotron Puzzle remains the best measured Heavy **capacity** candidate because
its earlier controlled long-generation result, 131K correctness, and tool gate
still stand. This recheck adds two important boundaries: the normal short
benchmark under-generates and should not replace the long-generation decode
number, while the current deterministic planning suite provides no quality
promotion evidence. Keep the candidate unpromoted pending a pinned stable
engine and broader quality calibration.
