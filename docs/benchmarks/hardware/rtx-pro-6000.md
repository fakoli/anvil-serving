# RTX PRO 6000 benchmark view

**Hardware:** 2× NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition,
96 GB each (192 GB aggregate), sm_120. **Host:** Fakoli Dark, Windows 11 with
Docker Desktop/WSL2. **Reviewed and last locally measured:** 2026-08-16.

> Side-by-side speed and recipe links for every configuration measured on this
> card or both cards in TP=2: [model comparison table](../comparison.md).

This page's result tables contain only measurements made on one or both PRO
6000 cards. Tests that merely kept a card running or described its topology belong in the
[mention audit](../rtx-pro-6000-audit.md), not the result tables.

## Recorded topology and service history

The two GPU roles are symmetric. Split mode can place independent workloads on
the cards. Exclusive TP=2 mode assigns both roles to one declared owner and
blocks every other inference workload until the mode is left; the cards are
connected over PCIe without NVLink, so 192 GB is aggregate rather than unified
memory. The public 2026-08-16 finding records the human-approved
DeepSeek V4 Flash 0731 Infernal Invocation r15 exclusive TP=2 text Primary.
The earlier Qwen3.8
single-service and split profiles remain retained history, while r33 is the
immediate managed DeepSeek rollback. Earlier findings also retain the r16
650K promotion, 262K retune, and r27 image-upgrade history. Active assignments
remain private operator state;
Fakoli Mini is model-free in the reference topology and reaches Dark remotely.

## Recorded promotion, rollback, and challenger state

| Order | Model | Decision | Contract |
|---:|---|---|---|
| 1 | [DeepSeek V4 Flash 0731](../models/deepseek-v4-flash.md) | `current`, 2026-08-16 r15 promotion | Infernal Invocation r15 B12X/DSpark K5 owns text Primary; exclusive TP=2, 393,216 tokens, eight admitted sequences, router concurrency two |
| 2 | [DeepSeek V4 Flash 0731](../models/deepseek-v4-flash.md) | immediate managed rollback | Exclusive TP=2 r33 text profile at 393,216 tokens on the same fixed endpoint; direct capacity through 359,900 actual prompt tokens |
| 3 | [Qwen3.8 27B](../models/qwen38-27b.md) | former single service and split | Retained official-FP8 SGLang text/image/OCR/video profile and FP8/BF16 vLLM split recipes |
| 4 | [Qwen3.5 122B](../models/qwen35-122b.md) | retained qualified recipe | Not started or selected by this promotion's restoration contract |
| 5 | [Agents-A1](../models/agents-a1.md) plus Omni | historical managed split restoration | Agents-A1 retains FP8 text/image/video evidence |
| 6 | [Laguna S 2.1](../models/laguna-s-2.1.md) | retained rollback-era recipe | Thinking disabled |
| 7 | [GPT-OSS Puzzle 88B](../models/gpt-oss-puzzle-88b.md) | retained rollback-era recipe | Strict unified-diff caveat |

## Comparable quality and context

### Exclusive dual-card TP=2

| Candidate | Repeated quality | Capacity and context evidence | Decision |
|---|---|---|---|
| DeepSeek V4 Flash 0731, Infernal Invocation r15 B12X + DSpark K5, batch 4,096, maxseq8, 393K | full direct functional gate; repeated tools/session/unified-diff/timeout 12/12; authenticated routed tools and OpenClaw-compatible Anthropic basic/tool paths pass | K5/no-spec 4K decode 150.0/76.4 tok/s and 32K decode 119.245/76.767; direct 351,118 and routed 340,119 actual prompt tokens pass; c8 short 8/8 and c2 long 2/2; 797,689 KV tokens / 2.03 full windows | human-approved `current` text Primary; r33 fixed-port managed rollback; actual Mini OpenClaw turn remains open |
| DeepSeek V4 Flash 0731, r33 B12X + DSpark K5, batch 4,096, maxseq16, 393K | prior repeated high-reasoning suite retained; routed functional checks passed except legacy long-needle calibration and trivial-prompt reasoning-evidence checks; OpenClaw/Hermes 393K/32K/high client paths passed | direct 238,507/339,310/359,900 actual prompt tokens passed; largest 65.2 s TTFT and 5,599 effective prefill tok/s; engine 725,543 KV tokens / 1.845 full windows | historical Primary; now managed TP=2 rollback; routed/OpenClaw/Hermes >300K and SWE score remain open |
| DeepSeek V4 Flash 0731, r33 B12X target-only/no-spec, batch 4,096 | functional preflight 6/6 at high reasoning; same repeated broad-quality suite not rerun | 119,503 actual prompt tokens; 17.364 s TTFT, 7,344 effective prefill tok/s, 75.20 decode tok/s; minimum-rank KV 15.99 GiB; engine reports 553,243 KV tokens with unresolved byte/token-accounting caveat | healthy direct-only A/B winner for capacity; next arm GPU-only 393K; `no-promotion` |
| DeepSeek V4 Flash 0731, r33 B12X target-only/no-spec | intelligence 6/6, session 3/3, tools 3/3 at high reasoning; functional preflight 6/6 | 119,503 actual prompt tokens; 17.445 s TTFT, 7,537 effective prefill tok/s, 73.86 decode tok/s; 283,917-token GPU KV allocation; context-target calibration caveat retained | priority `challenger`, `no-promotion`; 393K FP8-KV plus 16 GiB host-offload recipe translated but not loaded |
| Qwen3.5 122B NVFP4 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 2.32 s TTFT and 67.5 tok/s decode; 128K 4/4 at 14.59 s and 65.0 tok/s | TP=2 `no-promotion`; single-card profile remains `rollback` |
| Nemotron 3 Super 120B NVFP4, TP=2 + EP=2 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 2.84 s and 59.5 tok/s; 60K 4/4 at 5.58 s and 60.0 tok/s | `no-promotion` |
| Laguna S 2.1 NVFP4 | intelligence 6/6, session 3/3, tools 3/3 | 32K 12/12 at 1.97 s and 70.9 tok/s; 240K 4/4 at 31.85 s and 66.0 tok/s | TP=2 `no-promotion`; single-card profile remains `rollback` |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5 | coding/intelligence/session/tools 27/27; low/high/max functional gates pass | 128K pass; warmed 125,785-token row 19.44 s TTFO, 23.81 s visible TTFT, 128.9 tok/s decode; matched 4K decode 130.7 tok/s | priority intelligence `challenger`, `no-promotion`; DSpark preferred for experiments, both lanes fail 3 GiB reserve |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5, GPU-only Pi contexts | 650K/maxseq16 passed the low-reasoning gate plus Dark Pi, Mini Pi, and Mini OpenClaw high-reasoning smokes; 1M retained two fatal client-shaped workspace failures | 640K retrieval 120.6 s; matched 32K decode 141.6 tok/s at 650K; 1M qualification reached 985K before later client failures | 2026-08-02 promotion record; later retuned to 262K and upgraded to r27; 1M experimental only; 3 GiB reserve explicitly waived |
| DeepSeek V4 Flash 0731, remote AI-MBP25 benchmark worker | 8K context 1/1; tool-error retry protocol passed but final answer failed; SWE-bench Verified official-grader smoke resolved 1/1 | 6,102 observed prompt tokens in the 8K context case; larger buckets not attempted | benchmark substrate qualified for scout; no promotion change |
| DeepSeek V4 Flash 0731, r16 B12X + DSpark K5 + native KV offload | full functional preflight passes; 128K and 256K CPU reload proven | 8 GiB cold 249,573-token row: 43.75 s TTFO, 45.58 s visible TTFT, 5,705 effective prefill tok/s, 135.2 tok/s decode; 16 GiB exact 113,674-token replay: 113,408 external hits, 1.002 GB CPU-to-GPU, 0.825 s TTFO; managed mmap cleanup passes | capacity extension, `no-promotion`; no 256K per-card reserve sample |
| DeepSeek V4 Flash 0731, earlier SGLang lane | intelligence 6/6, session 3/3, tools 3/3 at low reasoning | 32K 11/12; 2.70 s TTFO, 29.11 s first-visible TTFT, 11.5 tok/s combined reasoning/visible decode | retained low-reasoning point-in-time lane; one reasoning-only exhaustion; see the r16 and r15 rows for later DSpark evidence |
| Inkling Small NVFP4 | intelligence 6/6, session 3/3, tools 3/3 at low reasoning | 32K 12/12; 2.79 s TTFO, 4.63 s first-visible TTFT, 73.5 tok/s combined reasoning/visible decode; reasoning-off lane also 12/12 | `no-promotion`; reasoning-off Responses caveat retained |

All rows used both physical cards as measured hardware, exclusive ownership,
one admitted request, and no co-resident inference. See the
[dated campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md) for
exact revisions, raw artifacts, protocol differences, and failure records.

#### DeepSeek 0731 research priority and r16 result

DeepSeek identifies 0731 as a re-post-trained official Flash generation with
the same 284B/13B-active target architecture as Preview and a bundled DSpark
draft module. Artificial Analysis independently scores the max-effort model at
50, number 3 of 101 comparable models, but reports 210 million evaluation
output tokens. That verbosity signal is consistent with the earlier 11/12
run's reasoning-only exhaustion and makes reasoning-budget policy a first-class
capacity gate.

The follow-up translated a pinned r16 B12X/InstantTensor recipe to WSL2 and
qualified the official release revision at 131K with DSpark K5. Low, high, and
max reasoning preflights passed, as did a 27/27 coding-agent slice and a warmed
125,785-prompt-token request. Against the same image and checkpoint without
speculative decoding, DSpark improved median per-request decode by 101.4% and
reduced median E2E by 58.8%. It also consumed 1.6-2.3 GiB more VRAM, and neither
lane retained 3 GiB reported free on each card. See the
[r16 qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md).

Current 0731-specific NVFP4 conversions are community artifacts. The strongest
conversion receipt reports TP=2 generation on two DGX Spark systems; the
strongest RTX PRO performance report uses four cards and an out-of-tree DSpark
path. Neither proves fit, quality, or speed on this two-card topology. See the
[research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
for pinned identities, benchmark deltas, conversion recipes, GGUF sizes, and
the publisher-reasoning/DSpark/NVFP4 qualification sequence.

### Single-card and historical profiles

| Candidate | Repeated quality | Context evidence | Decision |
|---|---|---|---|
| Qwen3.8 27B SGLang official FP8, remote AI-MBP25 worker | agentic smoke 2/2; agentic scout 16/18 with both failures in debug-loop; fixed SWE-bench Verified scout 5/5 officially graded and resolved | 393,216-token service; SWE tasks required 19-57 model requests and completed in 42m29s overall | bounded coding-agent evidence from the former service; larger stratified SWE and efficiency-aware debugging follow-up |
| Qwen3.8 27B SGLang official BF16 / official FP8 / Inferact NVFP4, MTP=3 multimodal | All pass the same earlier 18/18 image corpus; the official FP8 arm later passed direct 30/30 including video 14/14 and live admitted 28/28 | 393,216 configured tokens; at 4K BF16/FP8/NVFP4 measure 62.7/111.4/97.7 decode tok/s; official-FP8 direct video p50/p95 is 2.935/9.904 s | Official FP8 is the former human-promoted single service; NVFP4 is the lowest-latency third-party `no-promotion` alternative with video still open |
| Qwen3.8 27B official BF16 / official FP8 vLLM split | Both passed intelligence/session/tools at 3/3 and adaptive low/medium/xhigh control; final routed BF16 media 30/30 and 32-image request 1/1; all 16 TP/MTP matrix arms passed complete functional gates | Split TP=1 and exclusive TP=2 passed 388,979 actual tokens; TP=2 also passed 598,729 and 985,107 on both checkpoints, with 13.0-13.7 minute near-1M TTFT | managed rollback at TP=1/393K/MTP=3; TP=2 remains batch-like |
| Qwen3.8 27B SGLang official FP8 / Inferact NVFP4, MTP=3 | Both pass intelligence 6/6, session 3/3, tools 3/3, plus CPU-transport image/OCR; the later corpus adds repeated two-image ordering | Both pass 389K retrieval; at 4K official FP8 averages 111.3 decode tok/s while NVFP4 averages 98.1, with NVFP4 retaining lower TTFT and higher prefill | official FP8 former deployment; Inferact NVFP4 `no-promotion` |
| Qwen3.8 27B SGLang official FP8 / Inferact NVFP4 | Both pass full functional gates on two card placements plus intelligence 6/6, session 3/3, and tools 3/3; text-only, no speculation | Both pass 388,979 actual tokens; NVFP4 248.75 s TTFT / 1,564 prefill tok/s versus official FP8 258.13 s / 1,507; at 4K NVFP4 averages 57.9 versus 48.0 decode tok/s across five runs | NVFP4 text `challenger`; both `no-promotion`; former vLLM MTP=3 was faster and SGLang multimodal was unqualified on WSL2 |
| Qwen3.8 27B official FP8 MTP=4/5 | Both pass tools 20/20 and repeated deterministic intelligence/session/tools; the quality artifacts are behavioral rather than timing evidence | Both pass one cold 388,979-token request; cross-card 4K runs show MTP=5 only 0.4-1.3% above MTP=4 decode on a fixed card and no E2E win | MTP=4/5 `no-promotion`; MTP=3 remained the selected Qwen depth |
| Qwen3.5 122B NVFP4 | Passed protocol-v3, tools 10/10, image/OCR | 128K and 240K retrieval; 262,144 served | `rollback` |
| Laguna S 2.1 NVFP4 | Passed protocol-v3 with thinking disabled | 32K/128K/240K passed; TTFT 2.26/21.15/50.64 s | `rollback` |
| GPT-OSS Puzzle 88B | Tools/session/timeout 3/3; unified diff 2/3 | 32K and 128K retained | `rollback` |
| Agents-A1 | Official FP8 passed three-repetition protocol-v3; BF16/FP8 retain the identical 28/30 multimodal corpus | 240K on a 262,144 serve; 35.21 s promotion-quality TTFT, 32.97 s capacity TTFT p50 at 231,426 actual tokens | historical promotion |
| Gemma 4 12B QAT W4A16 | Historical protocol-v3 control passed | 240K passed | `no-promotion` |
| ThinkingCap Qwen3.6 27B FP8 | Historical strict-quality control passed | 240K retained | `no-promotion` |

## Capacity and recipe comparisons

| Model/configuration | Served context | Admission | Capacity note |
|---|---:|---:|---|
| DeepSeek V4 Flash 0731 Infernal Invocation r15 B12X + DSpark K5, batch 4,096, TP=2 | 393,216 | 8 engine; router 2 | FP8 compressed MLA KV, GPU-only; 797,689 reported KV tokens; 351,118 direct and 340,119 routed actual prompt tokens passed; c8 short and c2 at 99,175 tokens/request passed; matched no-spec control retained |
| Qwen3.8 27B SGLang official BF16 / official FP8 / Inferact NVFP4, TP=1 MTP=3 multimodal | 393,216 | 1 each | All pass the earlier 18/18 image corpus; official FP8 later passed video 14/14 and live admitted 28/28 at two images/one video; NVFP4 video plus broader concurrency, memory-pressure, and quality gates remain open |
| Qwen3.8 27B SGLang official FP8 / Inferact NVFP4, TP=1 MTP=3 | 393,216 | 1 each | Both pass 389K retrieval and cross-card gates; official FP8 averages 111.3 decode tok/s, NVFP4 98.1 with 0.448 s TTFT; CPU transport passes bounded image/OCR on both but full media and host-memory-pressure gates remain open |
| Qwen3.8 27B SGLang official FP8 / Inferact NVFP4, TP=1 no-spec | 393,216 | 1 each | Both pass 388,979 actual prompt tokens and cross-card functional gates; official FP8 reports 1,665,740 KV tokens and 48.0 tok/s 4K decode, NVFP4 1,805,068 and 57.9 tok/s; text-only because WSL2 CUDA-IPC multimodal warmup failed |
| Qwen3.8 27B official FP8 text, TP=2 control/MTP=3 | 393,216 / 600,000 / 1,010,000 | 1 | All three limits passed at 388,979 / 598,729 / 985,107 actual tokens; control reports ~4.65-4.67M KV tokens; MTP reports ~4.22-4.33M and 85.9-91.6 tok/s 4K decode; no P2P |
| Qwen3.8 27B official BF16 multimodal, TP=2 control/MTP=3 | 393,216 / 600,000 / 1,010,000 | 1 | All three limits passed; control reports ~3.77-3.78M KV tokens; MTP reports ~3.42-3.50M and 67.4-75.6 tok/s 4K decode; no P2P |
| Qwen3.8 27B official FP8 text | 262,144 control / 1,010,000 continuation | 5 control / 1 continuation | Control reports 1,825,809 KV tokens and 51 aggregate output tok/s at c5; 1M arm reports 1,845,432 KV tokens and passes 825,049 actual prompt tokens 3/3, but cold E2E is ~956.7 s |
| Qwen3.8 27B official BF16 multimodal | 262,144 | 2 | FP8 KV reports 1,036,311 tokens / 3.95 full windows; 241,250-token retrieval and 30/30 media corpus pass |
| DeepSeek V4 Flash 0731 r33 B12X + DSpark K5, batch 4,096, TP=2 | 393,216 | 16 configured; c1 long-context measured | FP8 DS-MLA KV, GPU-only; 725,543 reported KV tokens; 359,900 actual prompt tokens passed; managed exclusive rollback |
| DeepSeek V4 Flash 0731 r33 B12X target-only, batch 4,096, TP=2 | 131,072 measured; GPU-only 393,216 next | 1 | FP8 DS-MLA KV; 119,503 actual prompt tokens passed; lowering batch tokens reduced activation 34.1% and increased minimum-rank KV 15.27 to 15.99 GiB; engine reports 553,243 tokens, but >300K remains unproven pending a configured long-context request |
| DeepSeek V4 Flash 0731 r33 B12X target-only, TP=2 | 131,072 measured; 393,216 translated only | 1 | FP8 DS-MLA KV; 119,503 actual prompt tokens at 73.86 decode tok/s; 283,917-token GPU KV pool cannot support 393K without host capacity; translated recipe adds 16 GiB native offload |
| Agents-A1 FP8 multimodal | 262,144 | c1 at 262K; earlier 131K c32 | 188 tok/s decode at 8K c1; 156 tok/s decode and 32.97 s TTFT at 240K; 51.93 GiB KV; generated MoE tune rejected |
| Agents-A1 NVFP4 compact text | 131,072 | 16 | 198 tok/s at 8K c16; 128K c4 pass; vision excluded |
| Qwen3.5 122B NVFP4 | 262,144 | 1 | BF16 KV; near-ceiling prefill is slow |
| Laguna S 2.1 NVFP4 | 262,144 | recorded recipe | FP8 KV; disabled-thinking contract |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5, TP=2 | 131,072 | 8 configured; c1 measured | FP8 MLA KV; 130.7 tok/s matched decode; 128K pass; 3 GiB reserve failed |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5 GPU-only Pi, TP=2 | 650,000 historical / 1,000,000 experimental | 16 | 650K: 640K retrieval, 141.6 tok/s matched 32K decode, live Pi/OpenClaw smokes; 1M: retained client-shaped workspace failures; reserve waived |
| DeepSeek V4 Flash 0731 r16 B12X + DSpark K5 + native offload, TP=2 | 262,144 | 8 configured; c1 measured | 8 GiB cold 250K capacity; 16 GiB CPU tier proves 113,408-token external reload; per-card reserve not sampled |
| DeepSeek 0731 Vision (NVFP4), webbrain-one SGLang, TP=2 | 4,096 | 1; one image per request | ~175.6 GB mixed FP8/NVFP4 weights, 88.08/87.87 GB per card; `--mem-fraction-static 0.97` against an engine-measured KV floor of 0.9411; steady-state 95,164/93,992 MiB of 97,887 MiB; marlin MoE JIT first-compile requires a persistent tvm-ffi cache volume |
| GPT-OSS Puzzle 88B MXFP4 | 131,072 | 8 | FP8 KV; pinned Anvil vLLM |
| Nemotron 3 Super 120B NVFP4 | 131,072 | 5 | 1M advertised is not locally validated |
| Qwen3.6 27B community NVFP4 + MTP | 262,144 | 5 | 262K needle validated |
| Mistral Small 4 119B NVFP4 | 131,072 | 5 | Low short-request TTFT; weaker quality slice |

## External recipe watch and local follow-up

The [2026-08-15 Qwen3.8 recipe refresh](../../findings/2026-08-15-qwen38-27b-external-recipe-refresh.md)
recorded two test-next directions. The first is now complete: the
[MTP-depth qualification](../../findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md)
found no meaningful MTP=4/5 E2E win against the then-current official-FP8
TP=1/393K MTP=3 lane. SGLang's
commit-pinned cookbook supplies explicit RTX PRO 6000 official-FP8/BF16 cells,
SM120 FlashInfer guidance, 2,048-token prefill chunks, and GDN state-cache
sizing controls. The SGLang 200+ tok/s headline uses third-party NVFP4 and
DSpark artifacts and is not comparable with the local official-weight result.

Dormant vLLM MTP=4/5 recipes remain measured `no-promotion` controls. The
[SGLang official-FP8/NVFP4 qualification](../../findings/2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md)
completed the second direction with a digest-pinned runtime, exact revisions,
cross-card measurements, and exact restoration. NVFP4 improved the matched
no-spec SGLang lane. The later
[MTP/multimodal qualification](../../findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md)
raised official-FP8 decode to 111.3 tok/s and NVFP4 to 98.1, while bounded
image/OCR passed on both with CPU feature transport. The official-FP8 arm was
subsequently human-promoted as the single-service Primary/general-vision/OCR
profile and then qualified for one video; Inferact NVFP4 remains no-promotion.

## Recent changes

- 2026-08-16: after explicit human approval, the digest-pinned DeepSeek
  Infernal Invocation r15 K5 profile became the exclusive TP=2 text Primary at
  393,216 tokens. Matched K5/no-spec performance, 351,118-token direct and
  340,119-token routed retrieval, repeated quality 12/12, c8 short, c2 long,
  tools, streaming, Responses, exact managed routing, and rollback checks
  passed. The r33 393K profile is the fixed-port managed rollback. Martin
  Vit's upstream receipt covered 131,072 tokens on native Linux with two RTX
  PRO 6000 Blackwell GPUs on direct PCIe root ports; the 393K WSL2 result is
  independently qualified. Actual Mini OpenClaw remains open because the
  installed Mini controller lacks the current status tool.
- 2026-08-16: the then-current official-FP8 SGLang service passed direct
  media 30/30 with video 14/14. A managed router-only expansion added
  `vision.video` and fail-closed two-image/one-video admission; live admitted
  media passed 28/28 plus overflow, malformed-input, SSE, tool, and Primary
  regression gates. The model was not restarted and the second card remained
  dormant.
- 2026-08-15: the exact official-FP8 SGLang TP=1/393K/MTP `3/1/4` profile was
  human-promoted on one card, with the other card left empty. Guarded 108K and
  20-tool checks, direct+routed 18/18 media, routed Responses, and fresh
  Hermes/OpenClaw Primary turns passed. Initial admission was one request, two
  images, and no video; the former vLLM FP8/BF16 split remains rollback.
- 2026-08-15: a matched SGLang consolidation A/B added official BF16 to the
  MTP=3 CPU-transport comparison and ran 18 repeated media attempts per model,
  including two-image ordering. All three passed. Official FP8 cut media p50
  35.8% and raised 4K decode 77.7% versus BF16; NVFP4 cut media p50 51.1% and
  halved TTFT but decoded 12.3% slower than official FP8. Official FP8 is the
  preferred single-service challenger; the current split was restored and
  routed, with no promotion.
- 2026-08-15: SGLang EAGLE MTP `3/1/4` raised matched official-FP8 decode
  from 48.0 to 111.3 tok/s and Inferact NVFP4 from 57.9 to 98.1. Both passed
  389K retrieval, repeated deterministic quality, and bounded image/OCR after
  forcing CPU feature transport around the failing CUDA-IPC path. The exact
  vLLM split was restored and readmitted; no route or promotion changed.
- 2026-08-15: digest-pinned SGLang official FP8 and audited Inferact NVFP4
  both passed full functional, repeated deterministic-quality, cross-card 4K,
  and 388,979-token gates at TP=1/393K. NVFP4 averaged 22.6% lower TTFT and
  20.6% higher decode than the official SGLang control, but remained well
  behind current vLLM MTP=3 decode. The exact current split was restored and
  readmitted; both SGLang recipes remain `no-promotion` and text-only.
- 2026-08-15: official-FP8 MTP=4 and MTP=5 both passed functional,
  deterministic-quality, and 388,979-token gates. A cross-card swap showed
  the first-placement speed gap followed the GPU lane; MTP=5 beat MTP=4
  decode by only 0.4-1.3% on the same card and did not improve E2E. The exact
  MTP=3 FP8/BF16 split was restored and readmitted; MTP=4/5 remain
  `no-promotion`.
- 2026-08-15: external research queued a matched official-FP8 MTP=3/4/5
  vLLM A/B (now completed above) and an official-weight SGLang compatibility
  spike. Third-party
  NVFP4, DSpark, GGUF, AutoRound, and custom-runtime artifacts remain excluded.
  This is an `external-prior` update, not a new hardware measurement.
- 2026-08-14: human approval promoted the matched 393K TP=1/MTP=3 split.
  Official FP8 is the text Primary and official BF16 handles explicit
  general-vision/OCR with a 32-image request ceiling. Routed FP8 functional
  gates, BF16 media 30/30, one 32-image request, and Hermes/OpenClaw client
  paths passed without fallback.
- 2026-08-14: the matched Qwen3.8 BF16/official-FP8 matrix completed split
  TP=1 at 393K and exclusive TP=2 at 393K/600K/1.01M, each with control and
  MTP=3. All 16 arms passed full functional gates and cold retrieval at
  388,979/598,729/985,107 actual prompt tokens. MTP raised 4K decode 1.76-2.40x
  while consuming 7-11% of reported KV tokens; TP=2 cut 393K control TTFT
  35-38%. The exact 262K split baselines were restored; no route changed.
- 2026-08-14: the official Qwen3.8 27B FP8 checkpoint was configured for
  1,010,000 tokens on one card and passed retrieval through 825,049 actual
  prompt tokens at 3/3. Mean cold request-to-completion latency was 956.739
  seconds, so the result is stable offline/batch capacity rather than an
  interactive recommendation. The post-stress gate passed and the exact 262K
  FP8 lane was restored; decision remains `challenger`, `no-promotion`.
- 2026-08-14: official Qwen3.8 27B BF16 multimodal and official FP8 text
  checkpoints qualified on concurrent one-card split lanes. Both passed the
  functional, adaptive-reasoning, repeated-quality, 4K capacity, and 241,250
  actual-prompt-token gates; BF16 passed 30/30 media attempts. FP8 MTP=3 raised
  c1 decode 47.9 to 94.8 tok/s, prefix caching reduced a repeated 30K-prefix
  burst to 0.41 s TTFT, and unquantized KV halved full-window capacity without
  a 4K speed gain. Decision `challenger`, `no-promotion`; no route changed.
- 2026-08-11: after human approval, the r33 DSpark K5 GPU-only profile became
  `llm.primary` at 393,216 tokens, maxseq16, and batch 4,096. Direct capacity
  passed through 359,900 actual prompt tokens; OpenClaw and Hermes were aligned
  to 393,216 context, 32,768 output, and high reasoning, then passed client-path
  requests after gateway restarts. The
  legacy routed nominal-320K needle failed 413 because its conservative byte
  estimate exceeded the route limit, and the SWE smoke did not submit because
  installed benchmark profiles were missing. Both limitations remain explicit.
- 2026-08-10: a matched r33 A/B halved `max_num_batched_tokens` from 8,192
  to 4,096. Fresh bracketed starts reproduced the 8,192 baseline and measured
  a 34.1% activation reduction plus a 0.72 GiB minimum-rank KV increase. The
  4,096 arm passed the same 6/6 functional and 119,503-prompt-token capacity
  gates and was healthy/direct-only at campaign close. Its 553,243 reported KV tokens are
  not treated as >300K proof because KV bytes increased only 4.715%; the next
  gate is a GPU-only 393K configured serve and actual >300K request.
- 2026-08-10: the digest-pinned r33 B12X target-only control passed the full
  functional preflight, repeated high-reasoning intelligence/session/tool
  checks, and a 119,503-prompt-token request at 73.86 decode tok/s. The engine
  exposed 283,917 GPU KV tokens, below the 393K target; a quality-first 393K
  recipe retains FP8 DS-MLA KV and adds 16 GiB native host offload but was not
  loaded. Requested context targets were non-monotonic versus API-reported
  prompt tokens, so a benchmark-integrity ticket remains open. No route or
  promotion changed.
- 2026-08-07: a WebBrain DeepSeek 0731 vision-adapter (NVFP4) package
  first-loaded and served on TP=2 via SGLang, marlin/marlin kernels, and
  `--mem-fraction-static 0.97` against an engine-measured KV floor of 0.9411.
  ~175.6 GB of mixed FP8/NVFP4 weights split 88.08/87.87 GB per card, with
  steady-state usage of 95,164/93,992 MiB of 97,887 MiB; the first marlin MoE
  call required a persistent tvm-ffi JIT cache volume. Image conditioning was
  grounded, but OCR/GUI reading confabulated and the checkpoint has no chat
  template. Decision `no-promotion`; the 650K Primary was restored and
  verified healthy in the same session.
- 2026-08-03: AI-MBP25 completed the first managed remote context, agentic
  recovery, and SWE-bench Verified smoke against the unchanged 650K DeepSeek
  Primary. The 8K context and one-instance official grader paths passed. The
  tool-error case retried correctly but failed its final answer. This qualifies
  the worker and artifact path for a scout campaign without changing routes.
- 2026-08-02: after human approval, the 650K/maxseq16 profile became
  `llm.primary` with high reasoning as the client default and a generic
  per-tier 32,768 output cap. Dark Pi, Mini Pi, and Mini OpenClaw passed. The
  1M/maxseq16 profile was removed after two real client-shaped B12X workspace
  crashes, including one with only 5,120 requested output tokens.
- 2026-08-02: moving display output to the AMD iGPU allowed the DeepSeek 0731
  maxseq16 envelope to start. GPU-only 650K/maxseq16 and 1M/maxseq4/maxseq16
  passed near-limit retrieval and Pi protocol gates. The 650K profile is the
  preferred everyday Pi experiment; 1M/maxseq16 is the preferred explicit
  deep-session alternative. A retained
  1M/maxseq1 B12X workspace crash and sub-1-GiB post-workload free VRAM keep all
  profiles `no-promotion`.
- 2026-08-02: the derived DeepSeek 0731 WSL2 native-offload lane qualified a
  262,144-token serve through a 249,573-prompt-token request. A 16 GiB CPU tier
  also reloaded a 113,408-token external prefix after GPU eviction. An
  ownership-aware Anvil lifecycle now blocks cleanup while workers map the
  file and reclaims both 8 and 16 GiB mmaps after teardown. The decision remains
  `no-promotion`.
- 2026-08-01: the pinned DeepSeek 0731 r16 B12X lane qualified DSpark K5,
  low/high/max reasoning, 128K, and 27/27 coding-agent attempts. DSpark doubled
  matched decode versus same-image no-spec, but both lanes failed the 3 GiB
  reserve and remain `no-promotion`.
- 2026-08-01: DeepSeek V4 Flash 0731 became the priority intelligence
  challenger after official and independent research was reconciled with its
  exact local low-reasoning TP=2 evidence. The decision remains
  `no-promotion`; no new GPU run or route change occurred in the research pass.
- 2026-08-01: the hardware became a symmetric two-PRO topology. The exclusive
  TP=2 campaign qualified Qwen3.5, Nemotron 3 Super, Laguna S, DeepSeek V4
  Flash 0731, and Inkling Small without changing production aliases.
- 2026-07-29: Agents-A1 official FP8 passed the missing three-repetition
  protocol-v3 suite at the 262K profile and was promoted through the managed
  transaction. Qwen3.5 is now the immediate rollback.
- 2026-07-29: Agents-A1 official FP8 passed the same 262K/240K functional
  and capacity shape as Qwen. It used 35.31 versus 73.22 GiB model memory,
  halved 240K TTFT, and delivered the unchanged video corpus. Qwen passed all
  images but its exact NGC image lacked an H.264 decoder. Agents-A1 wins the
  bounded comparison; Qwen remains Primary pending matched repeated quality.
- 2026-07-28: Agents-A1 BF16/FP8 image and direct-video capability passed,
  but both reached only 28/30 on the strict multimodal corpus. NVFP4 qualified
  as a compact text-only profile. Isolated routed video passed after bounded
  thinking/error-classification fixes, and the FP8 tune was rejected; no route
  changed.
- 2026-07-28: Qwen3.5 122B became the human-gated Primary; Laguna moved to
  immediate rollback.
- 2026-07-27: Agents-A1 qualified as a thinking-disabled challenger without
  promotion.
- 2026-07-26: Laguna S 2.1 passed repeated quality and 240K retrieval.
- 2026-07-18: Puzzle established the pinned secondary recipe and strict-format
  caveat.

## Run history

The complete PRO 6000 history, including failed loads and incomplete runs, is
in the [run catalog](../runs.md#rtx-pro-6000-runs). Every row links its dated
finding and a stable [model dossier](../models/index.md).
