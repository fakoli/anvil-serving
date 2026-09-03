# Qwen3.8 27B quant and speculative-decoding comparison on RTX 5090

**Measured:** 2026-09-03 on one NVIDIA GeForce RTX 5090, 32,607 MiB.
**Decision:** Gittensor's target-only NVFP4/SGLang profile is the measured
TTFT winner and preferred new direct challenger; no promotion or route change.

This page compares the Qwen3.8 27B recipes that were actually started on this
card. The ranking follows the operator's declared order: time to first token,
decode rate, usable context/KV, then concurrency. It does not convert those
four dimensions into an invented composite score.

## Result

Gittensor's `Qwen3.8-27B-NVFP4-RTX5090` target-only SGLang arm produced the
lowest warm median TTFT at **50.9 ms**, passed the 244,002-token actual-prompt
request, completed the C2 workload at 128.7 aggregate output tok/s, and passed
the repeated bounded coding, triage, and tool checks. Its advertised DSpark
pair did not boot: draft CUDA-graph capture found incompatible target/draft
matrix shapes. The runtime also used default 1.0 FP8 KV scales because the
checkpoint did not provide calibrated scales. It is therefore a direct
latency challenger, not promotion evidence.

CometKim NInfer MTP3 was the decode winner at **228.0 tok/s** and retained a
full 262K service envelope, but failed the strict tool schema 0/3 by omitting
the required `zip` string. It cannot be the general-purpose winner. The
cdiamond iMatrix GGUF with embedded MTP8 is the most balanced fresh
full-context fallback: 223.1 ms TTFT, 96.0 tok/s decode, and a successful
244,002-token actual-prompt request.

The final source refresh found the newly published Unsloth Dynamic V3.0 NVFP4
target and separate MTP head. On the same pinned stock-vLLM 64K envelope,
MTP3 passed tools 20/20, reached 137.7 tok/s warm decode and 127.5 tok/s at a
53,706-token prompt, and left 3,198 MiB free. Its 388.7 ms warm TTFT and 64K
profile do not displace either the Gittensor TTFT winner or the cdiamond
full-context fallback. It is the strongest clean 64K speculative arm.

The exact Unsloth GGUF incumbent was restored after the campaign and passed a
fresh smoke, JSON, and 20/20 shared-prefix tool gate.

## Fresh warm 4K/C1 measurements

All rows used five warm requests with 3,613 API-reported prompt tokens. TTFT
and decode are medians. “Context proof” is the actual prompt tokens reported
by the endpoint in the retained long-context artifact, not the nominal
fixture size.

| Target / runtime | Speculation | Served context | TTFT | Decode | Context proof | C2 aggregate | Gate / interpretation |
|---|---|---:|---:|---:|---:|---:|---|
| Gittensor RTX5090 NVFP4 / SGLang 0.5.18 | none | 262K | **50.9 ms** | 79.5 tok/s | 244,002 | **128.7 tok/s** | **TTFT winner**; bounded quality/tools pass; FP8 KV scale caveat |
| cdiamond iMatrix NVFP4 GGUF / llama.cpp | none | 262K | 175.5 ms | 69.1 tok/s | 244,002 | 54.1 tok/s | full-context control |
| cdiamond iMatrix NVFP4 GGUF / llama.cpp | embedded MTP8 | 262K | 223.1 ms | 96.0 tok/s | 244,002 | 64.5 tok/s | balanced full-context fallback |
| incumbent Unsloth dynamic GGUF / llama.cpp | native MTP3 | 262K | 232.6 ms | 88.6 tok/s | prior 253,822 | not rerun | restored broader-capability incumbent |
| QUASAR QAT NVFP4 / vLLM 0.27.1 | none | 64K | 230.4 ms | 75.6 tok/s | 53,706 | 75.7 tok/s | fresh 64K control |
| QUASAR QAT NVFP4 / vLLM 0.27.1 | MTP2 | 64K | 267.4 ms | 116.0 tok/s | 53,706 | 82.5 tok/s | short decode gain; long decode regressed to 37.5 tok/s |
| CometKim NVFP4Full / NInfer | none | 262K | 315.6 ms | 85.1 tok/s | 244,002 | 51.3 tok/s | full-context control |
| CometKim NVFP4Full / NInfer | MTP3 | 262K | 326.3 ms | **228.0 tok/s** | 244,002 | 77.9 tok/s | decode winner; **tool schema 0/3** |
| Unsloth Dynamic V3 NVFP4 / vLLM 0.27.1 | none | 64K | 349.3 ms | 67.1 tok/s | 53,706 | 63.0 tok/s | current publisher target control |
| Unsloth Dynamic V3 NVFP4 / vLLM 0.27.1 | MTP3 | 64K | 388.7 ms | 137.7 tok/s | 53,706 | 70.5 tok/s | strongest clean 64K speculative arm; tools 20/20 |
| Telperion AutoRound / vLLM 0.27.1 | none | 64K | 328.3 ms | 61.8 tok/s | 53,456 | 59.0 tok/s | fresh 64K control |
| Telperion AutoRound / vLLM 0.27.1 | MTP2 | 64K | 365.5 ms | 103.0 tok/s | 53,456 | 64.5 tok/s | 86.3% token acceptance |
| Red Hat NVFP4 / vLLM 0.27.1 | none | 64K | 350.2 ms | 67.1 tok/s | 53,456 | 60.3 tok/s | fresh 64K control |
| Red Hat NVFP4 / vLLM 0.27.1 | MTP4 | 64K | 390.6 ms | 118.9 tok/s | 53,456 | 76.7 tok/s | strong 64K long decode; 66.7% token acceptance |

The first Gittensor warm request took 277.0 ms; the remaining samples drove
the 50.9 ms median, so its p95 is materially higher than its p50. Every loaded
arm passed its functional preflight and retained long-context request. C2 was
accepted by every fresh arm, but llama.cpp and NInfer were configured for one
active sequence and may queue rather than decode two requests simultaneously.
QUASAR also completed the retained C4 workload; that does not overcome its
third-place long-context decode regression.

## Speculation effects

| Matched target | TTFT change | Warm decode change | Long-context decode change | Outcome |
|---|---:|---:|---:|---|
| cdiamond MTP8 | +27% | +39% | 15.5 → 41.0 tok/s | Useful decode gain, slower first token |
| QUASAR MTP2 | +16% | +53% | 66.6 → 37.5 tok/s | Short win, long-context regression |
| CometKim MTP3 | +3% | +168% | 59.7 → 126.9 tok/s | Fastest decode, failed strict tools |
| Unsloth Dynamic V3 MTP3 | +11% | +105% | 63.8 → 127.5 tok/s | Strongest clean 64K MTP result; 83.2% token acceptance |
| Telperion MTP2 | +11% | +67% | 58.9 → 89.1 tok/s | Strong acceptance and decode gain |
| Red Hat MTP4 | +12% | +77% | 63.7 → 119.3 tok/s | Best 64K long-context decode |
| Gittensor DSpark | not measured | not measured | not measured | Rejected at startup for shape mismatch |

Speculative decoding consistently improved short decode for every compatible
pair, but none improved TTFT. That matters because TTFT was the primary goal.
The QUASAR result also shows why the advertised mechanism must be tested at
the intended context instead of inferred from a short prompt.

## Reserve and safety boundary

Before changing the normal reserve, the incumbent was unloaded and the card
was measured at zero reported GPU memory use, no compute or graphics
processes, 32,187 MiB free of 32,607 MiB, P8, and 0% GPU utilization. The
additional policy reserve was then set to zero for this dedicated campaign.
The 420 MiB total/free difference remained unavailable. This is a campaign-
specific exception, not a general reserve-policy change. Every candidate
still had to avoid OOM, CUDA errors, crashes, restarts, or unexplained request
loss.

## What should run next

1. Keep the restored Unsloth GGUF incumbent for broad-capability use.
2. Treat Gittensor target-only SGLang as the priority TTFT challenger.
3. Before promotion, supply or validate calibrated FP8 KV scales, rerun broad
   agentic/SWE and routed-client gates, and locate a publisher-confirmed draft
   that is dimensionally compatible with this exact target/runtime.
4. Keep cdiamond MTP8 as the full-context balanced fallback and Unsloth
   Dynamic V3 MTP3 as the strongest clean 64K speculative arm. Keep CometKim
   MTP3 as a text-decode research lead only until its tool schema passes.

The [dated finding](../findings/2026-09-03-qwen38-27b-rtx5090-quant-bakeoff.md)
contains the decision record and links to the complete
[raw evidence bundle](../findings/2026-09-03-qwen38-27b-rtx5090-quant-bakeoff-evidence/README.md).
