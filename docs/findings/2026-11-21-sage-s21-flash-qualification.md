# Sage S2.1 Flash versus GLM-5.3-Flash on two RTX PRO 6000 GPUs

**November 21, 2026 · Measured challenger · No promotion**

Sage's best result came with four concurrent requests. With a 65,536-token window and its embedded multi-token predictor (MTP), Sage showed the first visible answer **1.20 seconds sooner** than GLM and generated **4.2% more output tokens per second** across the batch. With two requests and a 327,680-token window, Sage again produced more tokens per second, but GLM showed the first visible answer **0.54 seconds sooner**. Both models passed the Python coding exercise and failed the Go exercise. These measurements support trying Sage for parallel work within 64K. GLM remains the better-supported general-purpose choice.

## What the timing means

Every row below used eight scored requests, about 7,684 actual input tokens per request, unique prompts, maximum reasoning effort, temperature 0.6 and top-p 0.95. Each pair used the same prompt generator and seed at the stated concurrency. Output lengths varied. The first visible token is the first piece of the answer a user could read, after any reasoning tokens. Decode speed is the median for an individual request while the batch was running.

| Model and configured window | Concurrent requests | First visible token, median | End-to-end, median | Per-request decode, median | Aggregate output |
|---|---:|---:|---:|---:|---:|
| GLM, 327K, DFlash2 | 4 | 6.668 s | 7.109 s | 58.62 tok/s | 110.40 tok/s |
| Sage R1, 64K, drafts off | 4 | 9.419 s | 10.760 s | 21.90 tok/s | 73.08 tok/s |
| **Sage R2, 64K, native MTP** | **4** | **5.465 s** | **6.177 s** | **56.84 tok/s** | **115.09 tok/s** |
| Sage R3, 327K, drafts off | 4 | 9.970 s | 13.934 s | 22.62 tok/s | 70.41 tok/s |
| GLM, 327K, DFlash2 | 2 | 3.603 s | 4.092 s | 76.14 tok/s | 91.85 tok/s |
| **Sage R4, 327K, native MTP** | **2** | **4.140 s** | **4.610 s** | **92.79 tok/s** | **103.77 tok/s** |

GLM generated 2,121 output tokens in its four-request-concurrency run and Sage R2 generated 2,402. The comparison describes a user-facing batch of natural answers. Fixed-output throughput and stable tail latency remain unknown. GPU and engine samples confirm the observed concurrency. R3's extra first-token event belonged to a warmup that ended before the scored interval. [Workload and sampling settings](2026-11-21-sage-s21-flash-qualification-evidence/workload-manifest.json) · [Concurrency, memory and power samples](2026-11-21-sage-s21-flash-qualification-evidence/raw/sampled-resources.json)

## Long prompts and answer quality

For long-context retrieval, each model received one request at a time, with the target near the beginning or the end of each prompt. All 12 cases passed. The four-slot and two-slot Sage configurations both reached **259,774 actual input tokens**. Simultaneous requests of that length and the full 327,680-token configured ceiling remain untested.

| Model | Shorter input | Early / late completion | Longer input | Early / late completion |
|---|---:|---:|---:|---:|
| GLM, DFlash2 | 64,293 tokens | 11.31 / 11.43 s | 259,991 tokens | 49.04 / 50.09 s |
| Sage R3, drafts off | 64,252 tokens | 11.44 / 11.59 s | 259,774 tokens | 49.57 / 50.59 s |
| Sage R4, native MTP | 64,252 tokens | 11.88 / 12.03 s | 259,774 tokens | 52.05 / 53.04 s |

- **Coding.** Sage R1, Sage R2 and GLM each passed the independent tests for Python affine-cipher and failed Go alphametics after two attempts. Two exercises establish a tie on those tasks. They cannot distinguish general coding quality.
- **Tools, context and images.** GLM and Sage R1/R2 each passed 6/6 agent tasks, 12/12 retrieval checks across 8K and 32K, and all 12 ordinary-image inputs (six cases repeated twice). R4 also passed the six agent tasks and 12 image inputs. Sage R1 passed all five functional preflight checks and returned 20/20 tool calls in the batch check. Scientific projectors were outside this workload.
- **Earlier benchmark overlap.** Sage and the two historical controls each passed the nine small reasoning/session checks. Sage and GLM retrieved all three small-context targets. The historical Mini control retrieved two and returned no visible answer at 6,144 tokens. There is no matched Mini timing result. The earlier Qwen3.8 27B campaign measured two independent replicas on a different workload. [Historical control receipt](2026-11-21-sage-s21-flash-qualification-evidence/raw/historical-comparator-receipt.json) · [Recipe and Qwen comparison](2026-11-21-sage-s21-flash-qualification-evidence/recipe-comparison.md)
- **Drafting.** On R2, six pairs of deterministic prompts produced identical visible answers and finish reasons with drafting on and off. With drafting enabled the engine accepted 214 of 298 proposed tokens (71.8%). With drafting disabled it proposed zero. Both sides had zero cached prompt tokens. The R1-to-R2 timing comparison also changes the cache layout. [Paired engine accounting](2026-11-21-sage-s21-flash-qualification-evidence/native/accounting/mtp-paired-engine.json)

## Reproducing the recipe

Both models used the same TensorFold 1.1.9 image on two 96 GB, 300 W RTX PRO 6000 Blackwell Max-Q GPUs. The Sage checkpoint was Mia's community EXL3 conversion at revision `e272f9573523b7dee7f1a5579c7b2997644d1c87`. Its routed expert weights use four bits per weight. We verified all 94 files and used Sage's own chat template. All four profiles used tensor parallelism 2 and FP8 KV. Sage's native MTP required an unsharded KV cache. R4's two long slots allocated 4.25 GiB of KV per rank. Four would require about 8.50 GiB, above the measured budget with the 4 GiB reserve in place. [Exact settings, runtime patches and alternative quants](2026-11-21-sage-s21-flash-qualification-evidence/recipe-comparison.md) · [Pinned checkpoint](https://huggingface.co/Mia-AiLab/Sage-S2.1-Flash-EXL3-4bpw/tree/e272f9573523b7dee7f1a5579c7b2997644d1c87)

## Exclusions and restoration

The strict fixed-output test produced **0/20 eligible requests on each model**, so the corresponding four-stream test was skipped. The fresh single-stream GLM natural run recorded nine first-token events for eight owned requests, with another request observed on the engine. Its timing is excluded from the comparison and the default performance charts. [Failure and coverage record](2026-11-21-sage-s21-flash-qualification-evidence/coverage-and-gaps.md)

All four Sage profiles were unloaded. The original GLM weights, image, recipe, GPU allocation and host binding were verified, and authenticated primary-route inference returned HTTP 200 with the expected visible answer **46 minutes 25 seconds** after the window opened. Direct engine metrics worked. Generation-bound monitoring was unavailable in the two hours before the trial and remained unavailable afterward because the current registration code rejects the all-GPU binding. [Restoration record](2026-11-21-sage-s21-flash-qualification-evidence/restoration.json) · [Complete evidence index](2026-11-21-sage-s21-flash-qualification-evidence/README.md)
