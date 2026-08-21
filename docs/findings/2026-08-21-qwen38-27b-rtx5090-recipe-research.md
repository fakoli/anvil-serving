# Qwen3.8 27B single-RTX-5090 recipe research and MTP3/ReplaySSM rejection

**Date:** 2026-08-21

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120

**Measured host:** Fakoli Mid Mod display label, isolated direct qualification lane

**Model:** `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`

**Stable served name:** `qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm`
**Decision:** no candidate is clearly better; retain the no-speculation 128K baseline and make no route or promotion change

## Outcome first

The current stock recipe remains selected. The best low-risk local candidate,
the same checkpoint with native MTP `3/1/4` plus SGLang ReplaySSM, improved
median decode from 76.54 to 138.19 tok/s at 4K and from 69.75 to 117.10 tok/s
at 64K. It also passed coding, JSON, a 49,549-token retrieval prompt, tools
20/20, streaming tools, tool-result continuation, and the Responses subset.

It nevertheless failed the hard route contract. SGLang loaded the checkpoint's
MTP component as a separate 5.73 GB draft model. ReplaySSM reduced recurrent
state replay to a few megabytes, but could not recover that draft-weight
reservation. Only 70,231 target and draft KV tokens remained, 46.4% below the
declared 131,072-token window. At 64K, faster decode did not improve the user-
visible result: median end-to-end latency regressed 1.9% because prefill was
6.0% slower and dominated the request.

The exact no-speculation baseline was restored through the managed recipe and
then passed coding, JSON, a 105,649-token retrieval prompt, tools 20/20,
streaming tools, tool-result continuation, and the Responses subset. No alias,
router policy, or deployment was promoted.

## Is the SGLang recipe a lie?

No, but the green **Verified** label is too easy to over-read. The current
[SGLang Qwen3.8 cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B)
validates the RTX 5090 cells at input length 8,192, output length 1,024, and
concurrency one. That establishes that the command can start and complete the
bounded cookbook workload. It does not establish that the configured model
window is actually allocatable, that a 128K prompt is admitted, or that tool
behavior and intelligence are unchanged.

The screenshot's exact RTX 5090 / Default / NVFP4 / Single Node / DFLASH2 /
High-Throughput / float32 selection was tested separately in this campaign.
It booted at the prescribed memory fraction 0.945, but exposed only 24,347 KV
tokens. The best BF16/single-slot/no-radix/no-prefill-graph tuning arm reached
70,262. Both are valid bounded short-context recipes and both are false as
128K replacement claims. See the separate
[DFlash2 diagnosis](2026-08-21-qwen38-27b-radixark-nvfp4-dflash2-rtx5090.md).

## Research method

Sources were reviewed on 2026-08-21 and classified by date, evidence type,
hardware and engine relevance, and decision impact. Current official sources
and physical-RTX-5090 measurements outranked social headlines. Reddit and X
were used as recipe leads, not as promotion evidence. Every lead was screened
against the actual objective: more usable context and better end-to-end speed,
with minimal bounded intelligence loss, equal-or-better tool calling,
multimodal retention, and safe rollback.

The complete dated registry is
[`source-registry.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/source-registry.json).
It covers the official SGLang matrix and ReplaySSM implementation, X, Reddit,
Hugging Face checkpoint cards, engine issues and fixes, independent hardware
sites, and custom-engine research. The normalized shortlist is
[`candidate-matrix.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/candidate-matrix.json).

## Candidate comparison

| Candidate | Context evidence | Speed evidence | Intelligence/tool evidence | Disposition |
|---|---:|---|---|---|
| Retained RadixArk/SGLang, no speculation | Local 131,072; restored at 105,649 actual prompt tokens | Local 76.54 tok/s decode at 4K; 69.75 at 64K; 11.16K/5.94K tok/s effective prefill | Local tools 20/20 plus streaming/continuation/Responses; prior MMLU-Pro 24/30; multimodal 30/30 | **Retain** |
| Same checkpoint, MTP3 + ReplaySSM | Local KV ceiling 70,231 | Local 138.19 tok/s at 4K; 117.10 at 64K; 64K E2E 1.9% slower | Same verified target weights; local tools 20/20 and complete API subset | **Reject as 128K replacement** |
| DFlash2 exact/tuned | Local 24,347 / 70,262 KV tokens | Diagnostic samples only; no retained matched throughput | Tuned tools 20/20; broader quality skipped after hard capacity fail | **Reject as 128K replacement** |
| EXL3 K5/K6 context/fidelity family | External physical-5090 evidence from 238.4K to native 262K, profile dependent | Strong decode, but fidelity profile prefill about 2.99K tok/s; its project reports no profile clears all six gates | Best external fidelity evidence: context edition 58/70 MMLU-Pro vs BF16 57/70 and broad KLD receipts; tool-schema tasks included | **Best research lead, not clearly better** |
| NInfer NVFP4 | External full 262K claim | About 202 tok/s headline | Bounded HumanEval/AIME parity; no comparable long-context reasoning or tool-quality gate | **Research lead only** |
| MiaAI vLLM TurboQuant KV + MTP3 | External full 262K claim | About 160 tok/s headline | Stock runtime malformed text/tool output in 13/15; unmerged patch fixes the bounded reproducer | **Wait for merged, pinned correctness fix** |
| gittensor ModelOpt NVFP4/vLLM | External full 262K without speculation | About 80.6 tok/s short; 74.3 near 61K | Tools 5/5 and small smoke only; independent same-suite divergence is materially worse than EXL3/official FP8 | **Insufficient quality proof for small speed delta** |
| calneymgp quantized-lm-head SGLang | External 134K card claim; scripts also name a 160K profile | 162.6 short; about 81 at 63K | Small bounded harness; card/scripts disagree on several recipe details | **Reconcile before local test** |

### Why EXL3 was not tested locally in this round

The current [EXL3 context card](https://huggingface.co/malaiwah/Qwen3.8-27B-EXL3-K5K6-context/commit/655085b6e9b58cff684c0b32ac24824547c63a3f)
has the strongest published balance of native context, distributional fidelity,
MMLU-Pro retention, and tool-schema evidence. Its accompanying
[reproducibility repository](https://github.com/malaiwah/qwen38-27b-exl3)
also publishes a self-contained custom-runtime image and detailed receipts.

Its own current matrix, however, disqualifies it from the strict replacement
test before a 20.7 GB model transfer and route interruption: the fidelity
profile reaches 238,400 tokens and strong decode but only about 2,988 tok/s
prefill, far below the local baseline's 11,160 tok/s at 4K and 5,936 tok/s at
64K. The throughput profile approaches baseline prefill and reaches 249,600
tokens, but fails the project's own KLD fidelity gate. The authors state that
no profile meets all six north-star criteria. A future EXL3 qualification is
reasonable only if a new pinned profile closes that prefill/fidelity frontier;
the current evidence does not justify calling it clearly better.

NInfer remains the highest-upside speed lead. Its
[engine repository](https://github.com/Neroued/ninfer) and
[NVFP4 artifact](https://huggingface.co/Ostfralla/Qwen3.8-27B-NVFP4-NInfer)
report full native context and roughly 200 tok/s on a 5090, but use an unmerged
custom runtime and do not publish a route-comparable tool-use and long-context
reasoning gate. The MiaAI recipe has a more serious correctness blocker:
[vLLM issue 40880](https://github.com/vllm-project/vllm/issues/40880) records
malformed Qwen3.8 MTP output, and the proposed
[fix remains a pull request](https://github.com/vllm-project/vllm/pull/40914).

## Matched local A/B

Both measured arms used the same GPU, target revision, served name, FP8 E4M3
KV, FlashInfer attention, 2,048-token chunked prefill, concurrency one,
thinking-disabled default, and CPU multimodal feature transport. The candidate
changed only the digest-pinned SGLang runtime required for current ReplaySSM,
enabled native MTP `3/1/4`, disabled radix and prefill graphs to preserve
workspace, and pinned one persistent Mamba state slot. Performance runs were
greedy, concurrency one, 256 output tokens, with ten 4K requests and three 64K
requests per arm.

| Measurement | Baseline 4K | MTP3/ReplaySSM 4K | Delta | Baseline 64K | MTP3/ReplaySSM 64K | Delta |
|---|---:|---:|---:|---:|---:|---:|
| Decode, p50 tok/s | 76.54 | 138.19 | +80.5% | 69.75 | 117.10 | +67.9% |
| Effective prefill, p50 tok/s | 11,159.99 | 10,447.09 | -6.4% | 5,936.30 | 5,581.27 | -6.0% |
| TTFT, p50 | 323 ms | 345 ms | +6.8% | 10,390 ms | 11,051 ms | +6.4% |
| Generation, p50 | 588 ms | 318 ms | -45.9% | 687 ms | 453 ms | -34.2% |
| End-to-end, p50 | 911 ms | 664 ms | -27.1% | 11,309 ms | 11,528 ms | **+1.9%** |

The result explains why peak decode headlines are insufficient for an agent
route. MTP materially helps token generation after the first token. At long
context, prefill and TTFT dominate, so a 68% decode gain can still produce a
slower completed request.

## Memory root cause

Startup evidence is retained in
[`mtp3-replayssm-startup-proof.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/mtp3-replayssm-startup-proof.json).
Target weights used 20.14 GB. The separate MTP draft used another 5.73 GB.
ReplaySSM did exactly what its
[merged implementation](https://github.com/sgl-project/sglang/pull/28695)
promises: intermediate SSM cache rounded to 0.00 GB, raw replay buffers were
about 0.005 GB, and one FP32 persistent state slot used 0.28 GB. The remaining
memory yielded 2.14 GB target KV and 0.14 GB draft KV, each capped at 70,231
tokens. The blocker is duplicated draft weight, not ReplaySSM state overhead.

This also reconciles the DFlash2 result. DFlash carries a much smaller draft
checkpoint but needs a large fixed verifier state allocation; MTP reuses a
native draft architecture but SGLang loads it as a separate 5.73 GB model.
Different mechanisms reach almost the same approximately 70K ceiling for
different reasons.

## Promotion rule and next useful trigger

No route should change unless one pinned candidate proves all of the following
on this exact lane:

1. at least the retained 131,072-token declared window and the 105,649-token
   actual retrieval gate;
2. lower end-to-end latency at both 4K and 64K, not decode-only speed;
3. tools 20/20 plus streaming, tool-result continuation, and Responses;
4. no material regression on the bounded thinking-enabled quality control and
   the established multimodal corpus;
5. startup headroom, exact identity, managed unload/load, and successful
   restoration.

The next test should be triggered by a material runtime change rather than a
new headline: in-checkpoint MTP weight sharing/offload in SGLang, a stable
full-context DFlash verifier allocation, a pinned EXL3 profile that closes the
prefill/fidelity tradeoff, or a merged and released vLLM Qwen3.8 correctness
fix. Until then, the no-speculation recipe is the only configuration that
satisfies the complete local contract.

## Evidence inventory

- [`baseline-capacity-4k.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/baseline-capacity-4k.json)
- [`baseline-capacity-64k.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/baseline-capacity-64k.json)
- [`mtp3-replayssm-capacity-4k.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/mtp3-replayssm-capacity-4k.json)
- [`mtp3-replayssm-capacity-64k.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/mtp3-replayssm-capacity-64k.json)
- [`mtp3-replayssm-functional-60k.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/mtp3-replayssm-functional-60k.json)
- [`baseline-restoration-after-mtp3.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/baseline-restoration-after-mtp3.json)
- [`mtp3-replayssm-startup-proof.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/mtp3-replayssm-startup-proof.json)
- [`source-registry.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/source-registry.json)
- [`candidate-matrix.json`](2026-08-21-qwen38-rtx5090-recipe-research-evidence/candidate-matrix.json)

The reproducible rejected candidate is
[`configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mtp3-replayssm-mm-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mtp3-replayssm-mm-recipe.toml).
Evidence was sanitized before publication; no private network identity, GPU
UUID, credential, or personal path is present.
