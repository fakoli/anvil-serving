# Local-model source guide

**Prepared:** 2026-10-13 from the completed source map. This is a curated
reading directory, not a benchmark result, deployment recommendation, or
compatibility claim. Links identify public material; a source's claim remains
bounded by its model revision, quantization, engine, hardware, context,
concurrency, prompting, and task protocol.

The curated starting set below has a complete public-safe appendix: [source directory appendix](source-directory.md) and [community addendum](community-addendum.md). They preserve public source links and their evidence limits while omitting private community material.

## How to use this guide

Use this order for a candidate: official model card and exact revision → exact
quant artifact → runtime/release support → a hardware-relevant firsthand report
→ a locally controlled task and resource measurement. Keep model quality,
runtime correctness, throughput, and capacity as separate questions.

Evidence labels used here:

- **Primary maintainer:** source code, release, model card, or official docs.
- **Benchmark owner:** methodology or task suite; useful for protocol, not
  necessarily a local-quant score.
- **Firsthand report:** a practitioner recorded an experiment; check topology
  and controls before reuse.
- **Lead:** a useful discovery pointer whose result needs primary-source or
  local verification.

## Start here: primary model and evaluation authorities

| Source | Usefulness | Evidence and boundary |
|---|---|---|
| [Qwen models](https://huggingface.co/Qwen) · [DeepSeek models](https://huggingface.co/deepseek-ai) · [Z.ai GLM](https://huggingface.co/zai-org/models) · [Xiaomi MiMo](https://huggingface.co/XiaomiMiMo/models) · [MiniMax](https://huggingface.co/MiniMaxAI) | Establish base lineage, license, artifacts, templates, and revisions. | **Primary maintainer.** A mutable card or organization page is not an immutable artifact pin or local fit result. |
| [Artificial Analysis](https://artificialanalysis.ai/) and its [methodology](https://artificialanalysis.ai/methodology/capability-indices) | Broad external capability priors and a stated aggregation method. | **Benchmark methodology.** Hosted/model-version scores do not measure a local quant or serving recipe. |
| [SWE-bench](https://www.swebench.com/) | Repository issue-repair task family and grader ecosystem. | **Benchmark owner.** Record split, agent, tool policy, budget, and verifier. |
| [Aider leaderboards](https://aider.chat/docs/leaderboards/) | Edit-format and multi-language coding signal. | **Benchmark owner.** Aider's 225-task score is not terminal-agent or repository-navigation evidence. |
| [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | Dated code-generation and execution tasks. | **Benchmark owner.** Keep the dataset window and generation protocol with any result. |
| [Terminal-Bench](https://github.com/harbor-framework/terminal-bench) | Terminal-agent workflows where tools, sandbox, and time limit matter. | **Benchmark owner.** Compare complete systems, not bare model names. |

## Quantization publishers and artifact methods

| Source | Usefulness | Evidence and boundary |
|---|---|---|
| [NVIDIA Model Optimizer](https://github.com/NVIDIA/Model-Optimizer) | Vendor conversion/calibration contracts and model-format support. | **Primary maintainer.** Support must be checked against the exact architecture and runtime. |
| [vLLM llm-compressor](https://github.com/vllm-project/llm-compressor) | Maintained vLLM compression/export path. | **Primary maintainer.** A supported exporter does not prove serving quality or capacity. |
| [Local Inference Lab GLM QAD FP8 branch](https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD/tree/fp8) | Mixed attention/expert precision and CSF artifact changes. | **Artifact publisher.** The card's lack of GPU/quality validation remains a material gap. |
| [Unsloth](https://github.com/unslothai/unsloth) | Current conversion and GGUF distribution ecosystem. | **Maintainer.** Treat model-specific benchmark claims independently. |
| [ISTA-DASLab GSQ-RCO](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) | A distinct Qwen quantization method and tradeoff lead. | **Artifact publisher.** Expert-pruned derivatives are a different model arm. |
| [Intel AutoRound](https://github.com/intel/auto-round) · [HQQ](https://github.com/dropbox/hqq) · [AWQ](https://github.com/mit-han-lab/llm-awq) | Quantization methodology and format context. | **Method sources.** They are not a current recipe or a workload result. |
| [bartowski GGUF](https://huggingface.co/bartowski/Qwen3.8-27B-GGUF) · [Ubergarm GGUF](https://huggingface.co/ubergarm/Qwen3.8-27B-GGUF) | GGUF artifact options and metadata. | **Artifact distributors.** Popularity and file size do not establish quality. |

## Runtime and kernel sources for RTX PRO 6000-class work

Hardware-relevant reports must distinguish **SM120 RTX PRO 6000** from other
Blackwell variants. A generic “Blackwell” claim can describe a different
compute capability, memory system, or power envelope.

| Source | Usefulness | Evidence and boundary |
|---|---|---|
| [NVIDIA CUDA GPU table](https://developer.nvidia.com/cuda/gpus) · [compute-capability guide](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html) | Verify architecture and platform constraints. | **Primary maintainer.** Beginning point for compatibility, not an engine validation. |
| [vLLM releases](https://github.com/vllm-project/vllm/releases) · [SGLang releases](https://github.com/sgl-project/sglang/releases) | Model integration, parser, cache, and serving changes. | **Primary maintainers.** Pin the released image/commit; do not infer support from main branch. |
| [FlashInfer releases](https://github.com/flashinfer-ai/flashinfer/releases) · [PR 6294](https://github.com/flashinfer-ai/flashinfer/pull/6294) | SM12x MoE, attention, cache, and correctness changes. | **Maintainer source.** A merged or open fix does not prove a packaged runtime selects that path. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) · [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) | GGUF, hybrid placement, CUDA support, and speculative paths. | **Maintainer/fork sources.** Pin the build and artifact; forks and upstream are separate deployment states. |
| [ExLlamaV3](https://github.com/turboderp-org/exllamav3) · [TabbyAPI](https://github.com/theroyallab/tabbyAPI) | EXL3 format, placement, cache behavior, and serving integration. | **Maintainer/project sources.** Rolling release does not establish production parity. |
| [CUTLASS Blackwell guide](https://docs.nvidia.com/cutlass/latest/media/docs/cpp/blackwell_functionality.html) · [FlashAttention](https://github.com/Dao-AILab/flash-attention) · [TileLang](https://github.com/tile-ai/tilelang) | Kernel layout, attention and DSL research when profiling identifies a bottleneck. | **Kernel sources.** Microbenchmarks must not be relabeled as model throughput or task quality. |
| [TensorRT-LLM quantization matrix](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html) · [DeepGEMM](https://github.com/deepseek-ai/DeepGEMM) | Vendor support boundaries and model-kernel dependencies. | **Maintainer sources.** Match exact quant, architecture, and kernel dependency. |

## Firsthand hardware and real-work reports

| Source / author | What it contributes | Recency and host-relevance limit |
|---|---|---|
| [gisenberg/local-model-eval](https://github.com/gisenberg/local-model-eval) | Repository repair rates, empty-patch failures, serving controls, and compact-Qwen comparison controls. | **Firsthand.** Older than this source pass but high value because it records failures. Reproduce power, runtime, context, concurrency, and task protocol. |
| [ambientlight RTX PRO 6000 bench](https://github.com/ambientlight/rtx-pro-6000-bench) | Larger SWE and sustained-context evidence. | **Firsthand adjacent topology.** Multi-GPU results do not establish one-card behavior. |
| [lengtsp Qwen3.8 benchmark](https://github.com/lengtsp/qwen3.8-27b-benchmark) | One-card latency and resource observations. | **Firsthand.** Single-user historical worklog; no transferable repository-quality claim. |
| [lukaLLM DSpark worklog](https://github.com/lukaLLM/deepseek-v4-flash-dspark-rtx6000pro) | Multi-turn coding records, draft placement, and memory accounting. | **Firsthand.** Different CPU/RAM and a limited task-quality screen. |
| [SSHdotCodes MiMo runtime](https://github.com/SSHdotCodes/mimo-v2.6-flash-pro6000) + [ProCreations artifact](https://huggingface.co/ProCreations/MiMo-V2.6-Flash-MOPD-NVFP4) | Concrete offload implementation and memory assumptions. | **Same publication chain.** Runtime, RAM envelope, and tool behavior still need independent checks. |
| [ormandj SM120 GLM runtime](https://github.com/ormandj/sglang-glm53-flash-sm120) + [quant artifact](https://huggingface.co/ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO) | Reproducible mixed-precision GLM profiles and artifact integrity. | **Maintainer/practitioner.** Multi-GPU and custom runtime; do not attribute a result to quant alone. |
| [turboderp DeepSeek EXL3](https://huggingface.co/turboderp/DeepSeek-V4-Flash-0731-exl3) | Artifact and EXL3 implementation context. | **Maintainer.** Artifact support is not an independent task-quality ranking. |
| [SharkWipf Swift EXL3](https://huggingface.co/SharkWipf/Swift-1.5-Qwen3.8-Flash-Next-exl3) | Max-Q-adjacent PLE-in-RAM and precision tradeoff lead. | **Author report.** Swift changes post-training; numbers do not establish repository quality. |
| [Strata report](https://research.strata2signal.com/flash-next-on-three-cards/) + [runtime](https://github.com/Niko1221/Strata) | Clear resource accounting and runtime confounds for Qwen Flash Next. | **Firsthand.** Drafting and batching differ; limited quality work does not transfer. The report publisher and runtime project are distinct. |
| [Ai4Users Qwen debugging](https://ai4users.com/Articles/1/Qwen-3.8-Flash-Next-NVFP4%3A-benchmark-results-and-the-broken-thinking-mode) | Practical thinking/template failure diagnosis. | **Firsthand lead.** Different runtime and hardware; isolate the mechanism locally. |

## Current bounded runtime and artifact leads

| Source | Usefulness | Evidence and boundary |
|---|---|---|
| [brandonmusic GLM tr3 4bpw](https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw) + [runtime publisher](https://github.com/brandonmmusic-max/glm-5.3-flash-exl3-4bpw) | Incumbent-quant/runtime publication chain; the current card r19/v84 identifies the same OCI family as the incumbent. | **Publisher.** External 600 W/overclock DFlash figures do not transfer to a 300 W Max-Q APC/no-spec configuration. No new-weights claim is made. |
| [NInfer upstream](https://github.com/Neroued/ninfer), [ninfer-all](https://github.com/iamwavecut/ninfer-all), [ninfer6000](https://github.com/lkarlslund/ninfer6000), and [WaveCut Q2_0 v3](https://huggingface.co/WaveCut/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-NInfer-v3) | Source and artifact leads for Flash-Next placement experiments. | **Maintainer/fork/artifact leads.** Both compared configs record 512 experts and 10 active; pruning is not established. No matched RAM bound or native TP2 evidence; two-device PP is not TP. |
| [Ignis](https://github.com/gpillon/ignis) + [Trellis a25 artifact](https://huggingface.co/gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis) | Host-streamed Flash-Next/Trellis serving lead. | **Maintainer/artifact lead.** One-GPU streamed fit and quality remain unmeasured; no SWE equivalence. The known image metadata supports a later immutable-pin check, not a result. |

## X, Reddit, and Discord: discovery with guardrails

- **X:** follow [Andreas G](https://x.com/andreas__g/status/2108586022791455065) for task-level reporting, [Stephen J Ronan](https://x.com/SJRonanMD/status/2108965359290294681) for source-linked kernel correctness investigations, [Ash Hart / TensorFold](https://github.com/ashhart/TensorFold) for model/platform qualification matrices, and [Elliot Arledge](https://x.com/elliotarledge/status/2109282873610244181) for kernel evaluation. These are public leads; each report needs its linked artifact or code before influencing a recipe.
- **Reddit:** [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/) and [r/LocalLLM](https://www.reddit.com/r/LocalLLM/) are useful for finding failure reports and reproducibility discussions. Start with the [DSpark one-card discussion](https://www.reddit.com/r/LocalLLaMA/comments/1vmt1y3/i_ran_deepseek_v4_flash_284b_dspark_on_one_rtx/) and [Strata/llama.cpp comparison](https://www.reddit.com/r/LocalLLM/comments/1x46wxm/benchmarked_strata_0125_and_0139_against_llamacpp/). Votes, screenshots, and token-volume anecdotes are not task-success evidence.
- **Discord:** public community discussions can surface newly published artifacts, precision experiments, and kernel regressions. Treat them as **leads only** unless there is a public artifact, source patch, or complete reproducible record. This guide deliberately omits message quotations, member/account details, and access-specific thread links.

## Compact operating checklist

For each source-derived hypothesis, capture: exact model revision; quant and
PLE/KV precision; engine/image/build; GPU type/count/power; host-memory
placement; context/concurrency; template/reasoning policy; workload, sample
count, failures, and end-to-end time. Do not call two reports independent if
one repeats the other's card, runtime README, or figures.

A model card proves identity. A runtime release proves a source change. A
firsthand benchmark proves one bounded configuration. Only a controlled local
run can establish a decision for another environment.

## Local campaign follow-through

Start with one exact artifact/runtime pair and its resource envelope, then one contract probe and an independent quality gate. This campaign's local outcomes supersede any pre-local absence-of-result note: Ignis R4 finished 2/5 overall, two graded/resolved and three empty. Neither that record nor the public source collection establishes a universal winner. The retained official-base GLM restoration is a continuity decision with a fresh 2/5 baseline and explicit client limits.
