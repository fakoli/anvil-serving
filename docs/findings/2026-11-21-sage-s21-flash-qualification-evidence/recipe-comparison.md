# Choosing a Sage S2.1 Flash recipe for two 96 GB GPUs

Sources checked November 21, 2026. The test machine had two 300 W RTX PRO 6000 Blackwell Max-Q GPUs. All four local Sage profiles used the same pinned weights and TensorFold image as one another. GLM used its own weights and DFlash2 draft model on that same image.

## Weights and external benchmarks

| Candidate | Evidence and relevance to this machine |
|---|---|
| Shanghai AI Lab BF16 and FP8 | The official files are about 643 GB and 328.5 GB respectively. Even the FP8 weights exceed the pair's total GPU memory before cache and runtime overhead. The publisher reports Sage ahead of GLM-5.3-Flash on Terminal-Bench 2.1 (87.9 vs 84.3), DeepSWE (67.9 vs 63.7) and BFCL v4 (75.5 vs 71.7). SWE-bench Pro is close (60.6 vs 60.3). These scores describe the publisher's models. [Official results and deployment guides](https://huggingface.co/internlm/Sage-S2.1-Flash-FP8) |
| Mia EXL3 4 bpw | The community publisher identifies the source revisions, supplies checksums and specifies exactly which weights are quantized. The four-bit figure covers the routed expert projections. We verified 83 shards totaling 163.59 GiB and used the checkpoint's own chat template. Mia's existing two-GPU GLM runtime supplied a hardware-matched starting point. All local Sage results use this quant. [Pinned weights and provenance](https://huggingface.co/Mia-AiLab/Sage-S2.1-Flash-EXL3-4bpw/tree/e272f9573523b7dee7f1a5579c7b2997644d1c87) · [Mia's TensorFold recipe](https://github.com/MiaAI-Lab/GLM-5.3-Flash-2x-RTX-PRO-6000-TensorFold/tree/fc1ee3a5381031e2357662b46c2fb44b20190429) |
| Bizuayeu mixed NVFP4 | The publisher measured 93.56% top-1 agreement and KL divergence of 0.0426 against BF16 on 128 documents. They report 19.9 output tokens/s with drafts off across two GB10 systems. This is evidence for a second quant on different hardware and a different TensorFold release. Local Max-Q performance and task success remain unmeasured. [Publisher's fidelity and serving results](https://huggingface.co/Bizuayeu/Sage-S2.1-Flash-Mixed-NVFP4) |

## Local recipe settings

All four profiles used tensor parallelism 2, paged FP8 KV, a 4 GiB device reserve, a 32,768-token server output ceiling, high default reasoning effort, ordinary image support and the Sage checkpoint's tokenizer and template. Natural benchmark requests set their own sampling and output budgets. TensorFold was version 1.1.9 at `f350cd3ea253a042e205746928271f9d8f71a82d`, with the Mia recipe revision `fc1ee3a5381031e2357662b46c2fb44b20190429`. Mia applied 110 author patches. The six Anvil telemetry patches produced tree `109491764fc5f711705c148da278a708f6c1aeea`. The exact runtime image was `sha256:2824d665f9334db0fe9868706cd26dc7bd2604974625a2602c74c78cf90b5886`. [Runtime build and patch catalog](../2026-11-13-tensorfold-mia-runtime-functional.md)

| Profile | `--context` per sequence | `--parallel` slots | Draft control | `TF_GLM_KV_SHARD` | Measured use |
|---|---:|---:|---|---:|---|
| R1 | 65,536 | 4 | `--no-drafts` | 1 | Same short-window setting as R2, with a sharded cache and drafts disabled |
| R2 | 65,536 | 4 | Embedded MTP, default on | 0 | Lowest four-stream visible latency among the Sage recipes tested |
| R3 | 327,680 | 4 | `--no-drafts` | 1 | Four-slot configuration and successful long single-request retrieval |
| R4 | 327,680 | 2 | Embedded MTP, default on | 0 | Long single-request retrieval and two-stream natural-output throughput |

The pinned engine requires an unsharded KV cache for Sage's embedded multi-token prediction (MTP). The R2 and R4 launches omit both `--no-drafts` and an external drafter. R2 allocated 516 pages (1.71 GiB per rank), and R4 allocated 1,282 pages (4.25 GiB per rank). At R4's window, an unsharded four-slot pool would take about 8.50 GiB per rank against a measured R2 post-reserve budget of 6.9 GiB. We kept the reserve and tested R3 with sharding and R4 with two slots. The 327,680 figure is the configured ceiling. The largest successful Sage request had 259,774 measured input tokens.

R1 and R2 change both the cache layout and the draft setting. A separate R2 control held the cache layout fixed and toggled drafting per request. Across six deterministic prompt pairs, the visible answers matched, every prompt had zero cached tokens, and the engine accepted 214 of 298 proposals with drafting enabled. [Native evidence and grading boundaries](README.md)

## Comparison with the earlier Qwen3.8 27B benchmark

The September Qwen campaign answers a different question about the same GPU pair. Two independent one-GPU SGLang replicas delivered 1,402–1,423 aggregate output tokens/s on a 100-request canary workload, using a 4K target, eight concurrent requests per replica and thinking disabled. Sage and GLM each used tensor parallelism across both GPUs, thinking enabled, roughly 7.7K actual input tokens and eight scored requests total at concurrency two or four. The Qwen result is relevant for serving many short requests. A quality or latency ranking across the campaigns would require running the same workload and reasoning settings. [Qwen3.8 27B measurements and topology](../2026-09-04-qwen38-27b-pro6000-possibility-plan.md)
