# DeepSeek V4 Flash 0731 research update

**Captured:** 2026-08-01. **Evidence:** `external-prior` reconciled with the
existing dual-RTX-PRO `functional`, `capacity`, and `quality` artifacts.
**Decision:** keep `deepseek-ai/DeepSeek-V4-Flash-0731` as a priority TP=2
`challenger`; `no-promotion`. This was a documentation-only research pass. No
serve, GPU mode, production alias, or router profile changed.

The machine-readable [source registry](2026-08-01-deepseek-v4-flash-0731-research-evidence/source-registry.json)
pins every source, observation date, evidence class, hardware/runtime
relevance, and decision impact used below. External measurements are advisory
priors and are not relabeled as local qualification.

## Identity and why 0731 is distinct

The canonical repository is
[`deepseek-ai/DeepSeek-V4-Flash-0731`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash-0731/tree/7872f01b1d1fe23eabc4c98b48bffcef5a386062)
at revision `7872f01b1d1fe23eabc4c98b48bffcef5a386062`. The immutable release-weight
commit is `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`; the later revision adds SGLang
cookbook documentation and does not replace the released weights.

DeepSeek describes 0731 as the official Flash release that supersedes Flash
Preview. The [July 31 API update](https://api-docs.deepseek.com/updates/)
states that the architecture and size are unchanged and that the model was
re-post-trained. The behavioral generation is therefore distinct even though
the serving architecture remains compatible with Preview. It also carries a
bundled DSpark speculative-decoding module.

The [V4 paper](https://arxiv.org/abs/2606.19348) describes a 284B-total,
13B-active target model with one-million-token context, hybrid Compressed
Sparse Attention and Heavily Compressed Attention, manifold-constrained
hyper-connections, and more than 32T pre-training tokens. Hugging Face and the
SGLang cookbook can report approximately 304B for the 0731 checkpoint because
that census includes the attached draft module. Treat 284B/13B active as the
target-model description and 304B as a checkpoint-plus-draft accounting view,
not as two conflicting 0731 architectures.

The publisher repository was approximately 155.4 GiB logical weight content
when observed, MIT licensed, and ungated. Within its first day it had 15,366
downloads, 1,352 likes, and 40 quantization descendants. Those mutable counts
are adoption signals observed on 2026-08-01, not capability evidence.

## Intelligence evidence

DeepSeek reports large agentic gains over Flash Preview:

| Benchmark | 0731 | Flash Preview | Absolute gain | Relative gain |
|---|---:|---:|---:|---:|
| Terminal Bench 2.1 | 82.7 | 61.8 | +20.9 | +33.8% |
| NL2Repo | 54.2 | 39.4 | +14.8 | +37.6% |
| CyberGym | 76.7 | 38.7 | +38.0 | +98.2% |
| DeepSWE | 54.4 | 7.3 | +47.1 | +645.2% |
| Toolathlon-Verified | 70.3 | 49.7 | +20.6 | +41.4% |
| Agents' Last Exam | 25.2 | 15.8 | +9.4 | +59.5% |
| AutomationBench Public | 25.1 | 10.8 | +14.3 | +132.4% |

The same publisher table places 0731 above V4-Pro Preview on every listed
agent benchmark. This is meaningful but not independently reproducible from
the card alone: public coding tasks used the unreleased DeepSeek Harness in
minimal mode, `max` reasoning effort, `temperature=1.0`, and `top_p=0.95`.
DSBench-FullStack and DSBench-Hard are internal datasets.

The strongest independent corroboration available on 2026-08-01 is
[Artificial Analysis Intelligence Index v4.1](https://artificialanalysis.ai/models/deepseek-v4-flash).
Its max-effort 0731 evaluation scored 50 and ranked number 3 of 101 comparable
models. The evaluation generated 210 million output tokens. That result
supports the intelligence claim while independently flagging unusually high
reasoning/output consumption.

## Reasoning and tool protocol

0731 has `low`, `high`, and `max` reasoning modes. The
[official thinking-mode contract](https://api-docs.deepseek.com/guides/thinking_mode/)
says hosted thinking defaults to `high`. Reasoning is returned separately in
`reasoning_content`. If a turn emits tool calls, the client must preserve that
reasoning content and return it in subsequent tool-result requests.

The checkpoint does not include an ordinary Jinja chat template. It ships a
dedicated encoder/parser and DSML tool grammar. A local OpenAI-compatible serve
therefore needs an exact DeepSeek-V4 tokenizer mode, reasoning parser, and
tool-call parser. Malformed-output recovery, reasoning-state continuation,
streamed tool assembly, and completion-budget exhaustion are hard gates for an
agent deployment rather than optional compatibility details.

DeepSeek recommends allowing as many as 384K output tokens for `high` and
`max`. That recommendation is not a practical default on the current 32K local
profile; it establishes why a progressive context-and-output-budget matrix is
required before headline max-effort quality can be claimed locally.

## Runtime and speculative-decoding status

The current [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash?features=tool_calling%2Creasoning&hardware=b300)
treats 0731 as the default Flash variant and documents the custom tokenizer,
reasoning/tool parsers, FP4 indexer cache, and DSpark. The published example is
not a dual-RTX-PRO recipe.

The [SGLang V4 cookbook](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4)
documents RTX PRO 6000 TP=2 for Preview Flash, while its 0731 verified matrix
lists 8x B200, 4x GB300, and 4x H200. Its SGLang 0.5.16 DSpark example uses the
bundled draft head and resolves the checkpoint default to five proposed tokens.
The existing local 0731 TP=2 result therefore closes a public workstation
hardware-matrix gap, but only for non-speculative SGLang at the recorded 32K
profile.

DSpark should be measured as an A/B, not assumed to be a general throughput
win. Upstream recommends identical prompts, sampling, concurrency, warmup, and
cache state for speculative and non-speculative legs while recording accepted
length, TTFT, TPOT, total throughput, memory, and stop rate. Interactive c1
latency and saturated aggregate throughput can move in different directions.

## 0731 NVFP4 conversion landscape

NVIDIA's official `nvidia/DeepSeek-V4-Flash-NVFP4` remains a conversion of
Flash Preview. It must not be presented as 0731. Three current community
artifacts explicitly derive from the 0731 release:

| Artifact | Provenance and reported validation | Applicability to this host |
|---|---|---|
| [`MJPansa/DeepSeek-V4-Flash-0731-NVFP4@64d64cd8`](https://huggingface.co/MJPansa/DeepSeek-V4-Flash-0731-NVFP4/tree/64d64cd89bc63a66aa46506da89d7821f7491c62) | Pins release weights `9e165c30`; reports 500K calibration tokens, complete tensor census, byte-identical packed expert weights, and a 175,535,844,088-byte output; basic vLLM TP=2 generation on two DGX Spark systems | Strongest conversion receipt; not dual-RTX-PRO performance or quality evidence |
| [`auroter/DeepSeek-V4-Flash-0731-NVFP4@17e0f9da`](https://huggingface.co/auroter/DeepSeek-V4-Flash-0731-NVFP4/tree/17e0f9da8257371654d458ba518659aa99954c86) | NVIDIA ModelOpt 0.45.0 flow; same-hardware source/NVFP4 perplexity and throughput measurements on 4x RTX PRO 6000 | Strongest SM120 recipe prior; TP=4 and an out-of-tree DSpark path do not prove TP=2 behavior |
| [`utarn/DeepSeek-V4-Flash-0731-NVFP4@ca20bac9`](https://huggingface.co/utarn/DeepSeek-V4-Flash-0731-NVFP4/tree/ca20bac907e9711b759fcebd214a2e58ba7bd857) | Declares ModelOpt NVFP4 from 0731 | No target-hardware qualification found; lower-priority compatibility candidate |

The Auroter report measured the following on four RTX PRO 6000 cards with
vLLM 0.26.0. These are creator-reported `external-prior` numbers:

| Configuration | Single-stream decode | Eight-stream aggregate | Perplexity note |
|---|---:|---:|---|
| Source MXFP4 through Marlin W4A16 | 118.3 tok/s | 670 tok/s | 5.178 / 5.189 |
| NVFP4 through Marlin W4A16 | 119.5 tok/s | 661 tok/s | 5.160 / 5.182 |
| NVFP4 native W4A4 Cutlass | 124.5 tok/s | 701 tok/s | 5.297; about 2.5% higher |
| NVFP4 plus DSpark, V2 runner | 208.4 tok/s | 663 tok/s | Speculative result used an out-of-tree SM120 branch |

The report says DSpark lifted single-stream decode substantially but was
approximately throughput-neutral at eight streams. It also identifies a
silent correctness/performance hazard: the main routed experts are converted
to NVFP4 while the bundled draft experts remain MXFP4. Without a prefix-aware
draft-routing patch, the server can run with zero draft acceptance. Any local
NVFP4+DSpark experiment must gate accepted length and source/draft tensor
routing rather than treating successful generation as sufficient.

The MJPansa output is approximately 163.5 GiB, leaving much less aggregate
VRAM for runtime graphs and KV state than the publisher checkpoint on two
96 GB cards. TP=2 fit is plausible but remains unproven on this exact host.

## GGUF and other conversions

[`unsloth/DeepSeek-V4-Flash-0731-GGUF@109848da`](https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF/tree/109848da2469efe1f1aab9e11acea08a065ccd4f)
publishes an unusually dense size ladder:

| Family | Published size range |
|---|---:|
| IQ1 | 82.5-86.9 GB |
| IQ2 | 90.9-96.8 GB |
| IQ3/Q3 | 104-128 GB |
| IQ4/Q4 | 137-155 GB |
| Q8 | 162 GB |

Q8 is only about 7 GB larger than the 155 GB Q4_K_XL because substantial
parts of the publisher mixed-precision checkpoint are not further compressed.
GGUF is a useful alternate-engine or one-card research lane, but no current
source establishes parity for DSML tools, reasoning-state continuation,
streaming, or DSpark on this host. It is not the first qualification path when
preserving 0731's agent intelligence is the objective.

## Reconciliation with local dual-PRO evidence

The existing local result used the exact current publisher revision through a
pinned SGLang 0.5.16 derived image on two RTX PRO 6000 Blackwell Max-Q cards in
exclusive TP=2, with every other inference workload offline. It used publisher
FP4-expert/FP8 weights, FP8 E4M3 KV, 32,768 served context, c1,
`reasoning_effort=low`, and no speculative decoding.

Functional gates passed smoke, JSON, 30K retrieval, tools 20/20, streamed
tools, tool-result continuation, and Responses. Repeated protocol-v3 quality
passed intelligence 6/6, session 3/3, and tools 3/3 with 4,096 reasoning
headroom. The final capacity run completed 11/12 with 2.705-second TTFO,
29.106-second first-visible TTFT, 7,818 tok/s effective prefill, 11.5 tok/s
combined reasoning/visible decode, and 7.60 tok/s aggregate output.

One request consumed all 2,048 completion tokens in reasoning and returned no
visible answer. This local failure and the independent 210-million-token
Artificial Analysis evaluation point to the same operational risk: 0731's
quality can be dominated by reasoning-budget policy. The checkpoint also lacks
FP8 KV scaling factors, so the recorded unscaled FP8 KV path retains an
accuracy caveat.

Raw local evidence remains in the immutable
[dual-PRO TP=2 campaign](2026-08-01-dual-pro-tp2-model-campaign.md), including
the [functional preflight](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-preflight-reasoning-low.json),
[extended tools](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-tools-extended-reasoning-low.json),
[quality result](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-quality-tp2-reasoning-low.json),
and [final capacity artifact](2026-08-01-dual-pro-tp2-campaign-evidence/deepseek-v4-flash-0731-capacity-32k-c1-reasoning-v4-max2048.json).

## Priority qualification program

1. Re-run the publisher TP=2 lane at `low`, `high`, and `max` with progressive
   output allowances and explicit visible-answer/finish-reason gates.
2. Expand served context progressively from 32K to 64K, 128K, and 256K while
   preserving reasoning headroom. Do not infer one-million-token usability.
3. Run SGLang DSpark off/on at c1 and c4, sweeping one through five proposed
   tokens and recording acceptance, memory, TTFO, first-visible TTFT, TPOT,
   aggregate throughput, and stop rate.
4. Qualify a pinned 0731 NVFP4 artifact only after source-revision, tensor,
   calibration, and draft-routing checks. Compare W4A16 and W4A4 quality before
   calling either Pareto-preferred.
5. Use independent repository repair, terminal, multi-step tools,
   tool-result recovery, structured output, long-session recall, malformed
   DSML recovery, and completion-budget tests rather than reproducing only
   publisher prompts.
6. Keep an API-versus-local control lane so post-training behavior can be
   separated from quantization, parser, engine, and reasoning-control effects.

## Decision boundary

0731 is the priority intelligence challenger for focused dual-card research.
The intelligence jump has official and independent support, and exact
publisher weights already have bounded local TP=2 functional, capacity, and
quality evidence. The current result does not qualify `high` or `max`, DSpark,
0731 NVFP4, context above 32K, concurrency above one, or general superiority
over the current Primary. Promotion remains separately human-gated.
