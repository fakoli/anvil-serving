# Model comparison table

Every model configuration measured on this hardware, in one place: what it ran on, how fast it
was, whether reasoning was on or off, and where the working recipe lives.

If you have one of these cards and want to know what actually runs on it and how well — start
here, then follow the recipe link.

## How to read this table

**Rates are not interchangeable.** Three different instruments appear in the `Output rate`
column, and mixing them produces nonsense. Each cell says which one it is:

| Tag | Instrument | What it means |
| --- | --- | --- |
| `long-gen` | Controlled long-generation decode | Sustained decode rate on a long answer. **This is the number to compare across models.** |
| `agg` | Short-output aggregate capacity | Total output tokens ÷ wall time on a short-answer batch. Dominated by scheduling and prefill; **not a decode rate**. |
| `c128` | Continuous-batching aggregate | Throughput under 128 concurrent requests. Answers a serving-density question, not a single-user one. |

A row with a small `agg` number is usually not a slow model — it is a short workload. The GPT-OSS
Puzzle rows below produced only 20 to 86 output tokens, so their `agg` figures are explicitly
unusable as decode rates.

**TTFT only means something with its depth and concurrency.** Cells name them wherever a row
mixes several, and a column names them once in its header where the whole column shares one
shape — note that a row's TTFT depth is often *not* its served context, because latency was
usually probed at 8K on a much larger window. A bare cell was measured at c1. A near-ceiling
TTFT (tens of seconds) is prefill-bound and is a *prefill-latency proxy*, not a latency
regression.

**Reasoning is part of the recipe, not a preference.** Some configurations are only qualified
with thinking disabled; forcing it on changes the result and, in several cases, exhausts the
completion budget and returns no visible answer.

**Engine spread is wide** — NGC vLLM 0.19 through 0.25.1, pinned nightlies, a custom Anvil fork,
and llama.cpp/q36. Rows measured on different engines are not clean comparisons even at the same
context and concurrency.

**A row is not always one run.** Where a model was measured across several campaigns, the cells
here may come from different dates and engine windows — the Qwen3.6 27B MTP row pairs a
2026-07-12 TTFT with a 2026-07-10/11 decode A/B, and the Gemma 12B QAT row pairs the 07-16
bakeoff capacity with the follow-up's equal-length diagnostic (that follow-up recorded 20/68
for the same capacity point, so the runs are close but not identical). Follow the dossier and
dated finding before treating a row as a single experiment. Full rules:
[Methodology and evidence](methodology.md).

---

## Current Primary and retained single-card profiles

GLM-5.3-Flash EXL3 K3 plus corrected DFlash2 K5 now owns both 96 GB cards as
the TP=2 text/image/OCR Primary at 524K/router-c16, with measured C2 at a
nominal 250K target. The former 1M GLM profile is the first same-model rollback.
RadixArk Qwen3.8 Flash Next NVFP4 is the retained video-capable rollback. The
DeepSeek Infernal Invocation profiles and the Qwen3.8 27B single-service/
two-service split remain retained historical recipes.

| Model / config | Status | Quant · KV | 4K median TTFT / E2E | Decode | Multimodal acceptance | Evidence |
| --- | --- | --- | --- | ---: | --- | --- |
| [GLM-5.3-Flash EXL3 K3 plus corrected DFlash2 K5](models/glm53-flash.md) | `current` text/tools/image/OCR Primary; exclusive TP=2 | EXL3 K3 · FP8 DS-MLA target KV · BF16 draft KV · fixed K5 | 0.974 / 1.269 s | **83.08 tok/s** | image/OCR pass; up to 16 images; no video | [524K xgrammar qualification](../findings/2026-08-31-glm53-xgrammar-524k-qualification.md) |
| [Qwen3.8 Flash Next RadixArk NVFP4](models/qwen38-flash-next.md) | immediate text/image/OCR/video rollback; exclusive TP=2 | ModelOpt NVFP4 · BF16 KV · MTP3 | **0.141 / 0.340 s** | **155.9 tok/s** | direct 30/30; live repeats 57/60 strict; four images or one video | [vision promotion](../findings/2026-08-26-qwen38-flash-next-vision-promotion.md) |
| [GLM-5.3-Flash TR3/EXL3 4 bpw, vision fixed K5](models/glm53-flash.md) | historical GLM rollback evidence; exclusive TP=2 | EXL3 4 bpw · NVFP4 DS-MLA KV · fixed MTP5 | 1.052 / 1.568 s | 72.8 tok/s | image/OCR pass; video disabled | [qualification](../findings/2026-08-29-glm53-cardillo-purtell-qualification.md) |
| [DeepSeek V4 Flash 0731, Infernal Invocation r18 DSpark K5](models/deepseek-v4-flash.md) | former text Primary; retained TP=2 evidence | B12X W4A8 · FP8 compressed MLA KV | aggregate TTFT/E2E not published | **142.1 tok/s** | text-only contract; routed and real-client acceptance passed | [promotion](../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md) |
| [Qwen3.8 27B official FP8, SGLang TP=1 MTP=3](models/qwen38-27b.md) | former Primary/general-vision/OCR/video service | Official FP8 · FP8 E4M3 KV | 0.577 / 0.962 s | **111.4 tok/s** | CPU transport; direct 30/30, live admitted 28/28; two images, one video | [video expansion](../findings/2026-08-16-qwen38-27b-video-router.md) |
| [Qwen3.8 27B official FP8, vLLM TP=1 MTP=3](models/qwen38-27b.md) | split rollback, text | Official FP8 · FP8 KV | 0.834 / 1.295 s | 93.6 tok/s | text only; routed tools 20/20 | [historical promotion](../findings/2026-08-14-qwen38-27b-split-promotion.md) |
| [Qwen3.8 27B official BF16, vLLM TP=1 MTP=3](models/qwen38-27b.md) | split rollback, vision/OCR | BF16 · FP8 KV | 0.884 / 1.584 s | 62.0 tok/s | historical routed 30/30; 32-image request 1/1 | [historical promotion](../findings/2026-08-14-qwen38-27b-split-promotion.md) |
| [Qwen3.8 27B Inferact NVFP4, SGLang TP=1 MTP=3](models/qwen38-27b.md) | `challenger`, `no-promotion` | ModelOpt NVFP4 · FP8 KV | **0.448 / 0.914 s** | 98.1 tok/s | CPU transport: image/OCR pass; broader media open | [qualification](../findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md) |

The BF16 32-image result is one request at concurrency one. It is not a claim
of 32 concurrent requests.

### Remote coding-agent comparison

Both campaigns used AI-MBP25 as the isolated worker and reached Fakoli Dark
through the router. They did not use the same model recipe, GPU count, harness
revision, or complete workload, so this is a bounded capability comparison—not
a statistically controlled model ranking.

| Subject | Serving topology | Agentic evidence | SWE-bench Verified evidence | What the comparison supports |
|---|---|---|---|---|
| [Qwen3.8 27B official FP8, SGLang TP=1 MTP=3](models/qwen38-27b.md) | 1x RTX PRO 6000 Max-Q active; second equal card idle | smoke 2/2; scout 16/18; `tool-recovery-error` 2/2; both failures were `debug-loop` protocol failures | 5/5 fixed scout tasks officially resolved; 19-57 model requests/task; 42m29s overall | strongest local bounded repository-agent evidence; broader SWE sample and efficiency-aware debug follow-up still required |
| [DeepSeek V4 Flash 0731 r16 DSpark K5](models/deepseek-v4-flash.md) | exclusive TP=2 on 2x RTX PRO 6000 Max-Q | `tool-recovery-error` 0/1: retry protocol passed, required final answer failed | `django__django-11099` 1/1 officially resolved | qualified the then-new worker/grader path; too small for an intelligence ranking |

The exact SWE overlap is only `django__django-11099`, which both models
resolved. Qwen additionally resolved the fixed pytest, scikit-learn, requests,
and SymPy tasks. The shared `tool-recovery-error` scenario favors Qwen in the
retained evidence, but the benchmark source and request controls changed
between campaigns. Treat that as a useful directional result, not a matched
head-to-head score. See the [Qwen scout](../findings/2026-08-15-qwen38-27b-agentic-swe-scout.md)
and [DeepSeek smoke](../findings/2026-08-03-deepseek-context-agentic-swe-smoke.md).

### Official-FP8 MTP-depth controls

MTP=4 and MTP=5 used the then-current FP8 TP=1/393K/maxseq1/batch4096 recipe and
were swapped across the equal cards. Values are means of each run's median;
three repetitions were taken in the first placement and two after the swap.

| Setting | Card role | TTFT | Prefill | Decode | E2E | Decision |
|---|---|---:|---:|---:|---:|---|
| MTP=4 | Compute A | 0.836 s | 4,319 tok/s | 97.9 tok/s | **1.276 s** | `no-promotion` |
| MTP=5 | Compute A | 0.844 s | 4,280 tok/s | **98.3 tok/s** | 1.281 s | `no-promotion` |
| MTP=4 | Compute B | 0.841 s | 4,288 tok/s | 90.4 tok/s | 1.313 s | `no-promotion` |
| MTP=5 | Compute B | 0.845 s | 4,274 tok/s | **91.6 tok/s** | 1.315 s | `no-promotion` |

The apparent first-placement winner reversed after the card swap. On a fixed
card, MTP=5 adds only 0.4-1.3% decode and makes E2E slightly worse. Both
settings passed 388,979-token retrieval and bounded behavioral gates. The
then-current MTP=3 Compute B control remained ahead at 93.6 tok/s and 1.295 seconds
E2E. See the
[MTP-depth qualification](../findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md).

### SGLang no-speculation controls

The digest-pinned SGLang A/B held TP=1, 393,216 context, one request,
FP8 E4M3 KV, FlashInfer, 2K prefill chunks, disabled prefix cache, text-only
mode, and no speculation fixed. Each model ran three repetitions on its first
card and two after the model placements were swapped.

| Model | Runs | TTFT | Prefill | Decode | E2E | Near-limit result | Decision |
|---|---:|---:|---:|---:|---:|---|---|
| Official FP8 | 5 | 0.554 s | 6,512 tok/s | 48.0 tok/s | 1.451 s | 388,979 tok pass; 258.13 s TTFT | qualified control, `no-promotion` |
| Inferact NVFP4 | 5 | **0.429 s** | **8,409 tok/s** | **57.9 tok/s** | **1.244 s** | 388,979 tok pass; **248.75 s TTFT** | text `challenger`, `no-promotion` |

NVFP4 reduced matched TTFT 22.6% and raised decode 20.6%; the ranking held
after the card swap. It still trailed the then-current vLLM MTP=3 service's 93.6
tok/s decode. SGLang multimodal is not qualified on this WSL2 host because its
default CUDA-IPC feature warmup failed; these rows are text-only. See the
[SGLang/NVFP4 qualification](../findings/2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md).

---

## Dual RTX PRO 6000 Blackwell Max-Q — 192 GB aggregate, exclusive TP=2

These rows used both 96 GB cards over PCIe without NVLink. Each candidate was
the sole inference owner; ordinary split-mode, Omni, voice, router, and other
model workloads were offline. The original campaign changed no production
alias; later rows record separately approved promotions.

| Model / config | Status | Quant · KV | Served · validated context | Reasoning contract | First-output latency | Effective prefill | Completion rate · decode | Recipe |
| --- | --- | --- | --- | --- | --- | ---: | --- | --- |
| [GLM-5.3-Flash EXL3 K3 plus corrected DFlash2 K5](models/glm53-flash.md) | `current` text/tools/image/OCR Primary; exclusive TP=2 | EXL3 K3 · FP8 DS-MLA target KV · BF16 draft KV · fixed K5 | 524,288 · 206,296-actual-token retrieval; measured C2 nominal 250K | low performance; high bounded quality | 0.974 s @4K; 59.67-59.80 s run medians @240K | retained artifacts | tools 20/20; image/OCR pass; 83.08 tok/s at 4K and pooled 69.99 at 240K; 2,493,817 KV tokens; C2 2/2 | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) |
| [Qwen3.8 Flash Next RadixArk NVFP4](models/qwen38-flash-next.md) | immediate text/image/OCR/video rollback; exclusive TP=2 | ModelOpt NVFP4 · BF16 KV · MTP3 | 262,144 · 253,703 actual prompt tokens with 8,192 output request | thinking disabled | 0.141 s @4K; 6.932 s @128K target; 29.214 s at full reserve | 18,096 tok/s @128K target; 8,684 at full reserve | tools 20/20; media direct 30/30/live 57/60; 155.9/114.7/112.9/102.0 tok/s at 4K/128K/254K/full reserve | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml) |
| [GLM-5.3-Flash vision fixed K5, 262K](models/glm53-flash.md) | preferred interactive GLM `challenger`, `no-promotion` | TR3/EXL3 4 bpw · NVFP4 DS-MLA KV · fixed MTP5 · visual tower | 262,144 · 250K target / 206,296 actual retrieval | low measured; high bounded quality | 1.052 s @4K; 32.838 s @128K | 3,138 tok/s @128K | image/OCR, tools 20/20, coding 15/15 high; 72.8/55.7 tok/s decode; 560,866 KV tokens / 2.14 windows | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) |
| [GLM-5.3-Flash text fixed K5, 262K](models/glm53-flash.md) | matched text control, `no-promotion` | TR3/EXL3 4 bpw · NVFP4 DS-MLA KV · fixed MTP5 | 262,144 · 128K performance; 524K companion validates 495,045 actual | low measured; high bounded quality on companion | 1.055 s @4K; 32.898 s @128K | 3,133 tok/s @128K | tools 20/20; 69.8/61.9 tok/s decode at 4K/128K | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) |
| [GLM-5.3-Flash no spec, 524K](models/glm53-flash.md) | preferred maximum-context/headroom GLM `challenger`, `no-promotion` | TR3/EXL3 4 bpw · NVFP4 DS-MLA KV | 524,288 · 495,045 actual retrieval and 497,976 actual long-tool prompt | low measured | 144.3 s retrieval E2E | near-limit rate retained in artifact | exact needle and valid tool pass; 1,603,111 reported KV tokens / 3.06 full windows | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) |
| [GLM-5.3-Flash fixed K5, 524K](models/glm53-flash.md) | single-user maximum-context experiment, `no-promotion` | TR3/EXL3 4 bpw · NVFP4 DS-MLA KV · fixed MTP5 | 524,288 · 495,045 actual retrieval and 497,976 actual long-tool prompt | low capacity; high bounded quality | 166.0 s retrieval E2E | near-limit rate retained in artifact | exact needle, valid tool, coding 15/15 high; 565,898 KV tokens / 1.08 full windows | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml) |
| [GLM-5.3-Flash no spec, 262K](models/glm53-flash.md) | matched reliability control, `no-promotion` | TR3/EXL3 4 bpw · NVFP4 DS-MLA KV | 262,144 · 128K performance | low measured | 1.127 s @4K; 30.891 s @128K | 3,422 tok/s @128K | tools 20/20; 42.7/43.5 tok/s decode; c16 short 16/16 at 33.69 aggregate output tok/s | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) |
| [DeepSeek V4 Flash 0731, Infernal Invocation r18 DSpark K5, 1M/maxseq8](models/deepseek-v4-flash.md) | former text Primary; retained TP=2 evidence | B12X W4A8 · FP8 compressed MLA KV | 1,048,576 · 1,040,063 actual prompt tokens | default thinking | matched capacity recorded in finding | matched K5/no-spec evidence retained | 142.1/129.5 tok/s decode at 4K/32K; repeated quality and real-client acceptance pass | [promotion](../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md) |
| [DeepSeek V4 Flash 0731, r33 DSpark K5, 393K/maxseq16](models/deepseek-v4-flash.md) | managed TP=2 rollback; historical Primary | B12X W4A8 NVFP4 MoE / FP8 dense · FP8 DS-MLA KV | 393,216 · 359,900 actual tokens direct | high-reasoning quality prior; routed functional pass with retained reasoning-evidence caveat | 65.2 s TTFT @359,900 tok | 5,599 tok/s | direct capacity pass; OpenClaw/Hermes 393K/32K/high client paths pass; client >300K open | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r33-b12x-dspark5-maxseq16-batch4096-393k-recipe.toml) |
| [Qwen3.8 27B official FP8, TP=2 control](models/qwen38-27b.md) | `challenger`, `no-promotion`; text-only | Official FP8 weights · FP8 KV | 393,216 / 600,000 / 1,010,000 · 388,979 / 598,729 / 985,107 actual tokens | thinking disabled | 154.8 / 321.2 / 784.1 s cold TTFT at the three limits; 0.71-0.72 s p50 at 4K | 2,512 / 1,864 / 1,256 tok/s at the limits | all functional/context gates pass; 48.8-49.2 tok/s 4K decode | [matrix recipes and evidence](../findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md) |
| [Qwen3.8 27B official FP8, TP=2 MTP=3](models/qwen38-27b.md) | interactive-text `challenger`, `no-promotion` | Official FP8 weights · FP8 KV · MTP=3 | 393,216 / 600,000 / 1,010,000 · 388,979 / 598,729 / 985,107 actual tokens | thinking disabled | 158.8 / 324.2 / 780.1 s cold TTFT at the three limits; 0.74-0.76 s p50 at 4K | 2,449 / 1,847 / 1,263 tok/s at the limits | all functional/context gates pass; **85.9-91.6 tok/s** 4K decode; 7-9% KV-token cost | [matrix recipes and evidence](../findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md) |
| [Qwen3.8 27B official BF16, TP=2 control/MTP=3](models/qwen38-27b.md) | multimodal-reference `challenger`, `no-promotion` | BF16 weights · FP8 KV · control or MTP=3 | 393,216 / 600,000 / 1,010,000 · 388,979 / 598,729 / 985,107 actual tokens | thinking disabled | control 168.7 / 344.1 / 820.6 s cold TTFT; MTP no consistent limit-TTFT win | control 2,305 / 1,740 / 1,201 tok/s at the limits | all functional/context gates pass; 35.4-36.1 control and 67.4-75.6 MTP 4K decode | [matrix recipes and evidence](../findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md) |
| [Qwen3.5 122B A10B NVFP4](models/qwen35-122b.md) | `no-promotion` TP=2; single-card profile remains `rollback` | ModelOpt NVFP4 · BF16 KV | 262,144 · 128K | off for matched gates | 2.32 s TTFT @29,804 tok; 14.59 s @125,444 tok | 12,821 / 8,570 tok/s | 12/12 @32K · 67.5 tok/s; 4/4 @128K · 65.0 tok/s | [campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml) |
| [Nemotron 3 Super 120B NVFP4](models/nemotron3-super-120b.md) | `no-promotion` | NVFP4 · FP8 KV · EP=2 | 65,536 · 60K | off for matched gates | 2.84 s TTFT @28,438 tok; 5.58 s @53,820 tok | 10,025 / 9,646 tok/s | 12/12 @32K · 59.5 tok/s; 4/4 @60K · 60.0 tok/s | [campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml) |
| [Laguna S 2.1 NVFP4](models/laguna-s-2.1.md) | `no-promotion` TP=2; single-card profile remains `rollback` | NVFP4 · FP8 KV | 262,144 · 240K | **must be off** | 1.97 s TTFT @29,834 tok; 31.85 s @231,457 tok | **15,134 / 7,252 tok/s** | 12/12 @32K · **70.9 tok/s**; 4/4 @240K · 66.0 tok/s | [campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5](models/deepseek-v4-flash.md) | priority `challenger`, `no-promotion`; reserve fail | B12X W4A8 NVFP4 MoE / FP8 dense · FP8 MLA KV | 131,072 · 128K | low/high/max functional; low measured | warmed 19.44 s TTFO; 23.81 s visible TTFT @125,785 tok | 6,469 tok/s from TTFO | 27/27 coding-agent; 128K pass; **130.7 tok/s** matched 4K decode | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5, 650K/maxseq16](models/deepseek-v4-flash.md) | historical human-approved Primary; reserve waived; retuned 2026-08-06 to 262,144/batched 8192 (functional preflight only); image upgraded 2026-08-07 to r27 (performance figures in this row were measured on r16) | same B12X/FP8 recipe · FP8 MLA KV | 262,144 (was 650,000) · ~640K at the 650K envelope | low measured; high-default client smokes pass | 2.66 s TTFO; 5.46 s visible TTFT @32K; 120.6 s near-limit retrieval | 8,793 tok/s @32K | Pi protocol; Dark/Mini Pi and Mini OpenClaw; 3/3 @32K · **141.6 tok/s** median decode | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5, 1M/maxseq4](models/deepseek-v4-flash.md) | deep-session Pi experiment, `no-promotion`; reserve waived for qualification | same B12X/FP8 recipe · FP8 MLA KV | 1,000,000 · ~985K | low measured | 2.96 s TTFO; 5.00 s visible TTFT @32K; 235.7 s near-limit retrieval | 7,901 tok/s @32K | Pi protocol pass; 3/3 @32K · **119.9 tok/s** median decode | [pinned recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq4-1m-recipe.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5, 1M/maxseq16](models/deepseek-v4-flash.md) | `rejected` for client-facing Primary; experimental capacity evidence | same B12X/FP8 recipe · FP8 MLA KV | 1,000,000 · ~985K | low measured | 2.92 s TTFO; 4.58 s visible TTFT @32K; 237.7 s near-limit retrieval | 8,012 tok/s @32K | Qualification passed, then real client shapes fatally exceeded B12X workspace twice; 3/3 @32K · **129.0 tok/s** median decode | [retained recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-1m-recipe.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5, 1M/maxseq1](models/deepseek-v4-flash.md) | `rejected` for Pi agentic use; retained failure | same B12X/FP8 recipe · FP8 MLA KV | 1,000,000 · ~985K | low measured | 3.25 s TTFO; 10.52 s visible TTFT @32K; 242.3 s near-limit retrieval | 7,195 tok/s @32K | 3/3 @32K · 13.0 tok/s median decode; fatal B12X workspace error on three-tool burst | [retained recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq1-1m-recipe.toml) |
| [DeepSeek V4 Flash 0731, r16 DSpark K5 + native offload](models/deepseek-v4-flash.md) | capacity `challenger`, `no-promotion`; reserve unmeasured | same B12X/FP8 recipe · FP8 MLA KV · 8/16 GiB CPU offload | 262,144 · 250K | low functional/capacity | cold 43.75 s TTFO @249,573 tok; reload 0.825 s TTFO / 1.974 s visible TTFT @113,674 tok | cold 5,705; reload 137,856 effective tok/s | 250K pass; 16 GiB lane: 113,408 external hits, 1.002 GB CPU-to-GPU in 0.344 s | [reload recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload16-wsl2-mmap-unpinned-recipe.toml) |
| [DeepSeek V4 Flash 0731](models/deepseek-v4-flash.md) | `challenger`, `no-promotion` | publisher FP4 experts / FP8 · FP8 E4M3 KV | 32,768 · 30K | `reasoning_effort=low` | 2.70 s TTFO; 29.11 s first-visible TTFT @21,144 tok | 7,818 tok/s from TTFO | 11/12 · 11.5 tok/s combined reasoning/visible | [campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml) |
| [Inkling Small NVFP4](models/inkling-small.md) | `no-promotion` | ModelOpt NVFP4 · BF16 KV/SWA | 32,768 · 30K | `reasoning_effort=low` | 2.79 s TTFO; 4.63 s first-visible TTFT @21,879 tok | 7,844 tok/s from TTFO | 12/12 · 73.5 tok/s combined reasoning/visible | [campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml) |

The three thinking-disabled rows use `capacity-v3`. DeepSeek and Inkling use
`capacity-v4-reasoning`, whose generation interval begins at the first
reasoning or visible delta. Their decode values include reasoning tokens and
are not visible-only rates. Inkling's separate reasoning-off 32K lane completed
12/12 at 2.84-second TTFT, 7,887 tok/s effective prefill, and 74.6 tok/s
visible decode. The one failed DeepSeek request exhausted 2,048 completion
tokens in reasoning without producing a visible answer.

The r16 DeepSeek row is a separate runtime and workload from the earlier
SGLang row. Its 130.7 tok/s figure is the median of three successful run-level
p50 decode values at 4K/c1 with a 2,048-token output cap. A same-image no-spec
control measured 64.9 tok/s: DSpark improved decode by 101.4%, aggregate output
by 70.5%, and E2E by 58.8%, while using 1.6-2.3 GiB more VRAM. Both profiles
failed the per-card 3 GiB reported-free reserve.

The 650K and 1M Pi rows use a separate matched three-request 32K/c1 capacity
shape with a 1,024-token output cap. Their decode values are run-level medians,
not controlled `long-gen` rates. Near-limit retrieval is a separate one-request
needle gate. Moving display output to the AMD iGPU enabled the larger graph
envelopes, but reported free VRAM fell to 797/805 MiB after the 650K workload
and 339/335 MiB after the preferred 1M/maxseq16 probe; maxseq4 measured 207/209
MiB in its separate run. After qualification, the 1M/maxseq16 profile also
crashed on two real agent shapes: 703.64 and 687.83 MiB workspaces were required
against 514.25 MiB available. The second used a 19,118-token Pi prompt and a
5,120 output cap, so router output clamping is not a sufficient 1M mitigation.
The r16 650K and r33 393K rows are historical promotion/capacity evidence.
The Infernal Invocation r15 393K row is now the human-approved text Primary
and uses the operator-approved AI-only policy without a separate
graphics/co-resident reserve gate.

The native-offload row uses a narrowly derived WSL2 image and a different
262,144-token admission ceiling. Its cold 250K result is capacity evidence,
not a matched speed comparison with the 131K row. The 8 GiB follow-ups reused
the GPU prefix cache. The separate 16 GiB run sized CPU above the measured GPU
KV tier and proved reload through unchanged GPU-hit counters, 113,408 new
external hits, and 1,001,721,600 new CPU-to-GPU bytes.

---

## RTX PRO 6000 Blackwell Max-Q — 96 GB, sm_120, 300 W

The card is **power-limited to 300 W (Max-Q)**; treat external reports from higher-TDP cards as
advisory only.

### Current serving chain

| Model / config | Status | Quant · KV | Context · adm. | Thinking | TTFT | Output rate | Recipe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [GLM-5.3-Flash EXL3 K3 plus corrected DFlash2 K5](models/glm53-flash.md) | `current` text/tools/image/OCR Primary | EXL3 K3 · FP8 DS-MLA target KV · BF16 draft KV · fixed K5 | 524,288 · router c16; measured C2 nominal 250K; 16 images, no video | low performance; high bounded quality | 0.974 s @4K; 59.67-59.80 s run medians @240K | 83.08 tok/s decode @4K; pooled 69.99 @240K | [524K xgrammar qualification](../findings/2026-08-31-glm53-xgrammar-524k-qualification.md) |
| [Qwen3.8 Flash Next RadixArk NVFP4](models/qwen38-flash-next.md) | immediate text/image/OCR/video rollback | ModelOpt NVFP4 · BF16 KV · MTP3 | 262,144 · c1; four images or one video; client 253,952+8,192 | thinking disabled in qualified Chat path | 0.141 s @4K; 6.932 s @128K target | 155.9 tok/s decode @4K; 114.7 @128K target; 112.9 @254K target | [vision promotion](../findings/2026-08-26-qwen38-flash-next-vision-promotion.md) |
| [DeepSeek V4 Flash 0731, Infernal Invocation r18 DSpark K5, 1M/maxseq8](models/deepseek-v4-flash.md) | former text Primary | B12X W4A8 · FP8 compressed MLA KV | 1,048,576 · router concurrency 1 | default reasoning | see dated promotion | 142.1 tok/s decode @4K; 129.5 @32K | [historical promotion](../findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md) |
| [Qwen3.8 27B official FP8, SGLang TP=1 MTP=3](models/qwen38-27b.md) | former Primary/general-vision/OCR/video service | Official FP8 · FP8 E4M3 KV | 393,216 · 1 request; 2 images/request; 1 video/request | server default disabled; chat caller override | 0.577 s median @4K | **111.4 tok/s decode** | [video expansion](../findings/2026-08-16-qwen38-27b-video-router.md) |
| [Qwen3.8 27B official FP8 + BF16 vLLM split](models/qwen38-27b.md) | former managed split | Official FP8/BF16 · FP8 KV | 393,216 · 1 seq each; BF16 32 images/one video | disabled default, caller override | 0.834 / 0.884 s median @4K | 93.6 / 62.0 tok/s decode | [historical promotion](../findings/2026-08-14-qwen38-27b-split-promotion.md) |
| [Qwen3.5 122B A10B NVFP4](models/qwen35-122b.md) | retained qualified recipe; not immediate restore | ModelOpt NVFP4 · **BF16 KV** | 262,144 · c1 | default **on**, per-request disable | 0.15 / 0.26 s p50/p95 @8K c1 · 68.91 s @231K, c1 matched lane | 60.3 tok/s decode @231K · 59.45 `agg` @8K c1 | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml) |
| [Agents-A1 official FP8 multimodal](models/agents-a1.md) plus Omni | managed split restoration | compressed-tensors FP8 · FP8 KV | 262,144 · c1 | **must be off** | 0.25 / 0.53 s @8K c1 · 32.97 / 33.44 s @231K c1 | 188.1 tok/s decode @8K · 155.8 tok/s decode @231K | [promotion-era evidence](../findings/2026-07-29-agents-a1-primary-promotion.md) |
| [Laguna S 2.1 NVFP4](models/laguna-s-2.1.md) | `rollback` | NVFP4 · FP8 KV | 262,144 | **must be off** | 0.07 / 0.55 s @c1 · 3.44 / 4.37 s @c8 · quality ctx 2.26 / 21.15 / 50.64 s @32K/128K/240K | 75.46 `agg` @c1 · 83.24 `agg` @c8 | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L1229) |
| [GPT-OSS Puzzle 88B](models/gpt-oss-puzzle-88b.md) | `rollback` | MXFP4 + Marlin MoE · FP8 KV | 131,072 · 8 seqs | `reasoning_effort` (`low` for gates) | 0.393 / 0.956 s @8K c1 · 0.766 / 1.075 s @8K c8 · 25.906 s @128K | 3.85 / 17.85 `agg` — **only 20 / 86 output tokens; not a decode rate** | [full recipe](gpt-oss-puzzle-88b-recipe.md) |

Qwen3.5 122B is the only row here on **BF16 KV**; preserve that exact rollback,
along with c1 admission and its one-image limit. Agents-A1 keeps thinking
disabled, four-image/one-video admission, and the rejected MoE tune inactive.

### Other evaluated candidates

| Model / config | Status | Quant · KV | Context · adm. | Thinking | TTFT | Output rate | Recipe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [GPT-OSS 120B](models/gpt-oss-120b.md) | `no-promotion` | MXFP4 + Marlin · FP8 KV | 131,072 | `reasoning_effort` | 655.67 / 1257.35 ms @8K · 28.9 s @128K needle | **183.2 `long-gen`** | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L113) |
| [Nemotron 3 Puzzle 75B NVFP4](models/nemotron-puzzle-75b.md) | `no-promotion` | NVFP4 MoE + MTP 3 · FP8 KV | 131,072 · 2 seqs | off | 458.93 / 492.91 ms @8K · 13.2 s @128K needle | **137.0 `long-gen`** (MTP 1.50× from 91.4) | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L1020) |
| [MiniMax M2.7 REAP 139B](models/minimax-m27-reap.md) | `no-promotion` | NVFP4 · FP8 KV | 65,536 · c1 | off (no parser) | 86 ms warm @8K · 14.3 s @64K | 97.2 `agg` @c1 (2,179 out tok) | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L984) |
| [Qwen3.6 27B community NVFP4 + MTP](models/qwen36-27b.md) | `no-promotion` | ModelOpt NVFP4 + MTP 3 · FP8 KV | 262,144 · 5 seqs | off for capacity | 0.63 s @c1 · 3.22 s @c5 · 26.5 s @131K needle | **95.0 `long-gen`** (MTP 1.36× from 69.9) | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L1057) |
| [Ornith 1.0 35B FP8](models/ornith-35b.md) | `no-promotion` | compressed-tensors FP8 · FP8 KV | 131,072 | off | 772 ms warm @8K · **13.1 s full 131K prefill** (fastest of its set) | 29.2 `agg` @c1 (**273 out tok over 10 req**) | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L950) |
| [Agents-A1 BF16 multimodal](models/agents-a1.md) | `challenger` · `no-promotion` | BF16 · FP8 KV | 131,072 · c16 text, media c1 gated | **must be off** | 0.30 / 0.35 s @8K c1 · 1.50 / 4.82 s @8K c16 · 11.99 / 12.08 s @128K c1 | 89.98 `agg` @c1 · 162.33 `agg` @c16 | [multimodal recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qualification-multimodal-recipes.toml) |
| [Agents-A1 ProtoLabs NVFP4 text](models/agents-a1.md) | `challenger` · `no-promotion` | NVFP4 → Marlin W4A16 · FP8 KV | 131,072 · c16; vision excluded | **must be off** | 0.27 / 0.32 s @8K c1 · 1.08 / 4.32 s @8K c16 | 104.58 `agg` @c1 · 197.93 `agg` @c16 compact | [compact recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qualification-nvfp4-compact-recipe.toml) |
| [Nemotron 3 Super 120B NVFP4](models/nemotron3-super-120b.md) | `no-promotion` | NVFP4 · FP8 KV | 131,072 · 5 seqs | both; 1,024 headroom recommended | 0.62 s @c1 · 2.52 s @c5 · 16.76 s @131K | 33.19 `agg` @c1 · 45.90 `agg` @c5 | `cand-nemotron3-super-120b` |
| [Mistral Small 4 119B NVFP4](models/mistral-small-4.md) | `no-promotion` | NVFP4 · FP8 KV | 131,072 · 5 seqs | `reasoning_effort`; 2,048 headroom | **0.30 s @c1** · 1.85 s @c5 · 51.90 s @131K | 57.82 `agg` @c1 · 67.04 `agg` @c5 | `cand-mistral-small4-119b-nvfp4` |
| [ThinkingCap Qwen3.6 27B FP8](models/qwen36-27b.md) | `no-promotion` | compressed-tensors FP8 · FP8 KV | 262,144 · 5 seqs | **on** by default (256 + 4,096 headroom); rates below captured with it off | 1.01 s @c1 · 4.66 s @c5 · 32.3 s @131K needle | 6.661 `agg` @c1 (45 out tok) · 7.92 `agg` @c5 (42 out tok) | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L3) |
| Qwen3.6 27B official FP8 | `no-promotion` | FP8 + MTP 3 · FP8 KV | 262,144 · 5 seqs | off for capacity | 1.59 s @c1 · 5.68 s @c5 · 32.9 s @131K | 5.627 `agg` @c1 (55 out tok) · 8.31 `agg` @c5 (53 out tok) | `cand-qwen36-fp8` |
| Unsloth Qwen3.6 27B NVFP4 | `no-promotion` | NVFP4 + MTP 2 · FP8 KV | 262,144 · 5 seqs | off for capacity | 968.07 ms @c1 (1 req) · 3.68 s @c5 | 10.497 `agg` @c1 — **1 req / 14 out tok; not a decode rate** · 15.21 `agg` @c5 (66 out tok) | `cand-unsloth-qwen36-27b-nvfp4` |
| [Qwen3.5 122B NVFP4, NGC 26.04](models/qwen35-122b.md) | `no-promotion` | ModelOpt NVFP4 · FP8 KV | 131,072 · c1 | off for gates | 223 ms p50 @8K · ~28 s @100K | 38.8 `agg` @c1 (10 req × 8K) | earlier candidate window |
| [Qwen3.5 122B MXFP4 / Marlin](models/qwen35-122b.md) | `no-promotion` | MXFP4 → Marlin W4A16 · FP8 KV | 131,072 · 2 seqs | off | 720.79 / 974.40 ms @8K · 25.8 s @128K needle | 30.57 `agg` | [registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml#L740) |

### Gemma 4 family — 2026-07-16 template bakeoff

All rows: vLLM 0.25.1, FP8 KV, 256K Heavy window, three attempts per check at 100% pass.
Capacity columns are mixed short-generation workloads — `agg`, not decode.

| Config | Status | Quality | 32K cap. c1 | 32K cap. c2 | Quality-ctx TTFT 32K / 128K / 240K | 1,024-tok diagnostic |
| --- | --- | --- | --- | --- | --- | --- |
| [Gemma 4 12B QAT W4A16](models/gemma-4.md) | `no-promotion` | **pass** | 1.52 s · 21 `agg` | 0.27 s · 54 `agg` | 6.96 / 44.61 / 97.33 s | 109.03 `long-gen` |
| Gemma 4 26B-A4B BF16 (`gemma-4-26B-A4B-it`) | `rejected` | fail (timeout triage 0/3) | 0.73 s · 36 `agg` | 0.31 s · 77 `agg` | capacity 11.93 s @120K · 34.07 s @240K | *not measured* |
| [Gemma 4 31B W4A16](models/gemma-4.md) | `rejected` (latency) | pass | 4.02 s · 7 `agg` | 0.41 s · 19 `agg` | 15.44 / 112.30 / 248.57 s | 57.8 `long-gen` — *07-17 probe: 1,024 tok on a **128K** serve, not this column's 256K* |
| Unsloth Gemma 4 12B NVFP4 | `no-promotion` | fail (tool 1/3) | 21 `agg` | 76 `agg` | 3.23 / 32.70 / 81.47 s | 98.86 `long-gen` |
| Unsloth Gemma 4 26B-A4B NVFP4 | `no-promotion` | fail (timeout triage 1/3) | **45 `agg`** | **122 `agg`** | 1.83 / 18.93 / 48.27 s | **191.46 `long-gen`** |
| Unsloth Gemma 4 31B NVFP4 | `no-promotion` | pass | 7 `agg` | 30 `agg` | 9.39 / 92.92 / 223.32 s | 51.49 `long-gen` |

Under 128 concurrent requests the ranking inverts — NVFP4 wins on density where it lost at c1:

| Runner · config | c128 @1K | c128 @8K | 8K p95 TTFT | c1 @1K | c8 @1K |
| --- | --- | --- | --- | --- | --- |
| Gemma 4 12B QAT W4A16 | 2,042 `c128` | 1,053 `c128` | 1.58 s | 71 | 578 |
| Unsloth 12B NVFP4 | **2,770 `c128`** | **1,526 `c128`** | 1.16 s | 65 | 516 |
| Unsloth 26B-A4B NVFP4 | **3,227 `c128`** | 1,466 `c128` | 1.27 s | 91 | 853 |
| Unsloth 31B NVFP4 | 1,720 `c128` | 799 `c128` | 2.37 s | 36 | 335 |

### Rejected or unmeasurable on this card

| Config | Outcome |
| --- | --- |
| [GLM-5.3-Flash adaptive K1-K5 plus ReplaySSM](models/glm53-flash.md) | `rejected` — 12/20 repeated tools and degenerate repeated `handle` output despite 71.1/59.5 tok/s decode at 4K/128K. Retained only as negative performance evidence. |
| [Laguna XS 2.1 NVFP4](models/laguna-s-2.1.md) | `rejected` — corrupted text and 0/20 tools with FP8 KV; stalled without it; SGLang path returned empty 131K needle. **No trustworthy numbers.** |
| [DeepSeek V4 Flash NVFP4, 2026-07-10 single-card attempt](models/deepseek-v4-flash.md) | historical `rejected` — NGC vLLM 0.19 rejected the architecture; nightly load aborted at shard 18/46. **Nothing measured in that lane; the 0731 TP=2 result above supersedes it for current compatibility.** |
| Gemma 4 31B native MTP | Incompatible — assistant projection dims 6400 vs 10752. |

---

## RTX 5090 — 32 GB, sm_120

The current qualification lane uses the 5090 exclusively for one candidate.
The older Omni rows describe historical Fakoli Dark reservation shapes; their
27,999 MiB usable budget after a 4,608 MiB system/audio reserve is not the
current qualification policy.

| Model / config | Status | Quant · KV | Context · adm. | Thinking | TTFT | Output rate | VRAM |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Qwen3.8 27B Unsloth GGUF Q4_0 + MTP3, llama.cpp](models/qwen38-27b.md) | preferred 5090 `FAST-TIER` challenger, `no-promotion` | Q4_0 · Q4_0 KV · Q4_0 MTP3 | 262,144 · c1; 253,822 actual prompt + 8,192 reserve | disabled | 0.29 s short; 246.72 s mean at 253,822 actual | 104.1 tok/s short decode; tools 20/20; images 18/18 | 25,408 MiB startup; 25,460 MiB late |
| [Qwen3.8 27B RadixArk NVFP4, SGLang TP=1](models/qwen38-27b.md) | preferred 5090 `challenger`, `no-promotion` | ModelOpt NVFP4 · FP8 E4M3 KV | 131,072 · c1 | disabled | decode TTFT not measured; 119,675-token retrieval 29.8 s E2E | decode not measured; media 30/30; eight images / two videos 4/4 | 20.14 GB weights; 3,928 MiB free after startup |
| [Nemotron Nano Omni 30B NVFP4](models/nemotron-omni-30b.md) | `current` topology | NVFP4 · *KV not recorded* | 65,536 · 2 seqs | off for text gates | 122 / 164 ms p50/p95 | 224.08 `agg` @c2 | 27,706 MiB observed; exclusive |
| [Qwen2.5-Omni 3B](models/qwen25-omni-3b.md) | `challenger` · `no-promotion` | *not recorded* | 32,768 (recipe) · 2 seqs | n/a | 0.04 / 0.06 s p50/p95 | 243 `agg` @c2 | 24,576 MiB reserved; co-resident |
| [Gemma 4 E4B FP8-Dynamic](models/gemma4-e4b.md) | `no-promotion` (historical Fast) | FP8-Dynamic · FP8 KV | 32,768 | — | 0.46 s @c1 · 0.58 s @c2 | 49 `agg` @c1 · 79 `agg` @c2 | *not measured* |
| Gemma 4 E2B W4A16 | `no-promotion` | W4A16 · FP8 KV | 131,072 | — | 0.43 s @c1 · 0.21 s @c2 | 96 `agg` @c1 · 204 `agg` @c2 | *not measured* |
| Gemma 4 31B W4A16 (Fast lane) | `rejected` | W4A16 · FP8 KV | 64K practical | — | 2.60 s @30K c1 · 8.81 s @c2 | 9 `agg` · 8 `agg` | 128K needs 6.35 GiB KV, only 4.28 available |
| Gemma 4 26B-A4B BF16 (`gemma-4-26B-A4B-it`) | `rejected` | BF16 | — | — | *not measured* | *not measured* | 48.57 GiB model; **negative 5.74 GiB KV headroom** |

Both Omni probes used the same shape (6/6 requests, c2, 2,048-token prompt, 128-token cap) but
were reported at different precision, so their TTFTs are not comparable at millisecond
granularity.

### 2026-07-10/11 bakeoff — historical

Every row here is `historical-invalid` for exact rerun: no pinned checkpoint revisions. Per
[methodology](methodology.md), `historical-invalid` does not establish promotion evidence, so
**do not rank on these numbers** — they are retained because a negative or partial result is
still evidence.

Rate cells in this table come from a 10-request, 256-token-cap `agg` harness. Batch shape is
**not** uniform across the page: request count, concurrency, and the completion cap all vary by
campaign. Compare `agg` values only within a table, and only when the row notes agree; follow the
row's dated finding for its exact shape.
Both llama.cpp rows additionally ran with a warm prefix cache (~0.87–0.90 hit rate),
which inflates their aggregates relative to the two *measured* vLLM rows in this table, neither
of which records prefix reuse.

| Config | Engine | Served context | Output rate | Warm TTFT @8K | Verdict |
| --- | --- | --- | --- | --- | --- |
| Qwen3.5 35B-A3B Q4_K_M | llama.cpp | 64K | 56.279 `agg` @c1 (424 out tok) | 178 ms | fast-tier candidate; not promoted |
| Gemma 4 E4B QAT UD-Q4_K_XL | llama.cpp | 64K | 96.96 `agg` @c1 (401 out tok) | **61 ms** | low-latency specialist; not promoted |
| Nemotron Nano Omni 30B | vLLM nightly | 65,536 | 27.3 `agg` @c1 (236 out tok) | 675 ms | keep experimental |
| Nemotron 3 Nano 30B (text) | NGC vLLM 0.19 | 131,072 | 15.0 `agg` @c1 (322 out tok) | 1.68 s | keep experimental |
| Gemma 4 31B IT NVFP4 | vLLM gemma4-unified | — | *not measured* | *not measured* | **rejected** — all six configs died at KV allocation |

---

## Audio — STT and TTS

All audio rows were measured **on the RTX 5090**; the PRO 6000 is never the measuring device
for these numbers. The STT qualification additionally records the PRO 6000 as protected and
running during the run; the TTS round-trip source does not mention that card either way.

STT ran a shared 30-case corpus (`stt-corpus/v1`, 170.4 s): **24 human recordings are the primary
quality set; 6 synthetic phrases are reported separately and must never be merged in.**

| Model | Status | Primary-human WER | Sequential p50 / p95 | Sequential req/s | Concurrency-4 p95 | c4 req/s |
| --- | --- | --- | --- | --- | --- | --- |
| [Parakeet TDT 0.6B v3](models/parakeet.md) | `current` | **3.343%** | 72.35 / 177.87 ms | 12.80 | 240.43 ms | 21.00 |
| [Qwen3-ASR 0.6B](models/qwen3-asr.md) | `challenger` · `no-promotion` | 3.621% | **67.40 / 113.58 ms** | 14.86 | **137.36 ms** | **47.25** |
| [Nemotron 3.5 ASR 0.6B](models/nemotron35-asr.md) | `rejected` | 6.685% | 121.60 / 225.45 ms | 7.87 | 747.82 ms | 8.68 |

Qwen3-ASR is 36.1% faster at sequential p95 and within the declared one-point non-inferiority
margin — but Parakeet stays routed. Meeting the margin does not authorize a route change.
Nemotron 3.5 regressed 3.343 points, exceeding the margin.

| Model | Status | Stage latency | RTF | Round trip |
| --- | --- | --- | --- | --- |
| [Kokoro TTS](models/kokoro.md) | `current` | 289.27 ms | 0.1006 | 710.68 ms total = 421.41 ms STT + 289.27 ms TTS, WER 0.0 |

Neither Parakeet nor Kokoro has a pinned checkpoint revision recorded — identity is pinned at the
image level only. STT RTF and isolated per-model VRAM were never measured.

---

## Where the recipes live

- **[`configs/serve-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)** — 31 recorded recipes with quantization, context, flags, and environment. **9 of the 31 pin an image by `sha256:` digest; 20 pin a mutable tag** (`vllm/vllm-openai:nightly`, `lmsysorg/sglang:latest`) **and 2 record no image at all**, so those 22 cannot be reproduced exactly. Inspect one locally:

    ```bash
    anvil-serving models recipes show <model>
    ```

- **[`examples/fakoli-dark/`](https://github.com/fakoli/anvil-serving/tree/main/examples/fakoli-dark)** — the compose files and serve manifests behind the reference topology.
- **[GPT-OSS Puzzle 88B recipe](gpt-oss-puzzle-88b-recipe.md)** — the one model with a full standalone operator procedure, because it needs a pinned engine fork.
- **[Model dossiers](models/index.md)** — per-model status, identity, decision boundary, and dated history.
- **[Legacy recipe and gotcha page](models.md)** — retained for existing deep links; the dossiers win on any conflict.

## What is deliberately absent

Rows carrying `not measured` are gaps in the evidence, not zeros. Notably: standalone prefill
throughput is not published anywhere (long-context TTFT is used as a labelled proxy instead),
controlled long-generation decode was never captured for the Qwen3.5 122B rollback, and
STT real-time factor was never measured for any ASR model.

Several configurations lack a pinned checkpoint revision and therefore cannot be re-run exactly —
MiniMax M2.7 REAP, Ornith 1.0 35B, the historical 2026-07-10 DeepSeek V4 Flash
attempt, Parakeet, Kokoro, and the whole 2026-07-10/11 bakeoff set. They are
kept because a negative or partial result is still evidence,
but they cannot ground a new equivalence claim.

Three 2026-07-12 deterministic planning scores (Qwen 1/5, Nemotron 0/5, GPT-OSS 0/5) are retained
as `historical-invalid`: the harness let hidden reasoning consume the entire completion budget.
The repository forbids using them for ranking, and so should you.

External benchmark data never appears in these tables. It is an advisory prior for choosing what
to test, not a local result — see [External benchmarks](../EXTERNAL-BENCHMARKS.md).
