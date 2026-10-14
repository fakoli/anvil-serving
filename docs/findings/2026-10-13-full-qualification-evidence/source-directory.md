# Public source directory appendix

**Prepared 2026-10-13.** This appendix is the public-safe, comprehensive
reading index distilled from the completed source map. It lists 92 core public
sources; the eight-source [community addendum](community-addendum.md) completes
the 100-source public directory. It is not a benchmark result or deployment
recommendation. It omits all Discord channel/message links, quotations,
account/session material, and private-machine information.

**Reading rule.** Start with a publisher’s pinned artifact or maintainer source,
then inspect the exact runtime and method, then use a firsthand report to form a
local hypothesis. X and Reddit are discovery evidence until their linked code,
weights, traces, or protocol can be checked. An author’s self-report is never
elevated to an independent result.

**Date key.** “Observed 2026-10-13” is the source-map observation date, not a
publication date. “Unknown” means the completed source map did not retain a
reliable publication date. Age is assessed on 2026-10-13: current (Oct 2026),
recent (Sep 2026), older (Aug 2026), historical (before Aug 2026), or unknown.
Hardware relevance is to an RTX PRO 6000-class SM120 local inference target;
“adjacent” means the source should not be used as a direct capacity or quality
claim.

## Public practitioners, maintainers, and hardware reports

| Source | Published / observed; age | Evidence type and hardware/runtime relevance | Decision impact |
|---|---|---|---|
| [gisenberg/local-model-eval](https://github.com/gisenberg/local-model-eval) | Tested 2026-08-09; older | Firsthand repository-repair report on one RTX PRO 6000-class system; records failures and controls. | Strong protocol/control reference; reproduce locally. |
| [SSHdotCodes MiMo runtime](https://github.com/SSHdotCodes/mimo-v2.6-flash-pro6000) + [ProCreations MOPD artifact](https://huggingface.co/ProCreations/MiMo-V2.6-Flash-MOPD-NVFP4) | Runtime 2026-10-06; current | Maintainer/practitioner implementation chain for offload and RAM accounting; CPU differs. | Treat as one chain, not corroboration; gate local fit. |
| [lukaLLM DSpark worklog](https://github.com/lukaLLM/deepseek-v4-flash-dspark-rtx6000pro) + [Reddit discussion](https://www.reddit.com/r/LocalLLaMA/comments/1vmt1y3/i_ran_deepseek_v4_flash_284b_dspark_on_one_rtx/) | 2026-08-16; older | Firsthand one-PRO-6000 throughput/placement report; task-quality screen limited. | Capacity and runtime lead, not quality ranking. |
| [ormandj SM120 GLM runtime](https://github.com/ormandj/sglang-glm53-flash-sm120) + [mixed GLM artifact](https://huggingface.co/ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO) | Observed 2026-10-13; current | Public maintainer runtime/artifact for TP2+ SM120; custom runtime and multi-GPU. | Pin exact image/artifact; do not attribute results to quant alone. |
| [turboderp ExLlamaV3](https://github.com/turboderp-org/exllamav3) + [DeepSeek EXL3](https://huggingface.co/turboderp/DeepSeek-V4-Flash-0731-exl3) | Active 2026-10-12; current | Maintainer source for EXL3, placement and cache behavior. | Implementation authority, not independent quality evidence. |
| [Andreas G Terminal-Bench report](https://x.com/andreas__g/status/2108586022791455065) | Published 2026-10-09; current | Public firsthand task report; one Max-Q but much larger host-RAM envelope. | Method lead only; capacity does not transfer. |
| [ChristopheVandePoel ngram ablation](https://github.com/ChristopheVandePoel/flash-next-ngram-tests) | 2026-10-11; current | Firsthand Qwen PLE ablation on DGX Spark; public small-task protocol. | Separate token cost from correctness; adjacent hardware only. |
| [Stephen J Ronan bug report](https://x.com/SJRonanMD/status/2108965359290294681) + [FlashInfer PR 6294](https://github.com/flashinfer-ai/flashinfer/pull/6294) | 2026-10-09/10; current | Public source-linked kernel correctness investigation. | Inspect the exact path before assigning a model failure to it. |
| [ambientlight RTX PRO 6000 bench](https://github.com/ambientlight/rtx-pro-6000-bench) | Date unknown; observed 2026-10-13 | Firsthand larger SWE/context work on adjacent multi-GPU topology. | Useful workload evidence; no one-GPU inference. |
| [nerkyor Coder390](https://huggingface.co/nerkyor/Qwen3.8-27B-Coder390-EfficientThink-Opus5.5-GPT6Astra-Grok4.7-DSV4Pro-K3-SFT-RLOO-MTP-DFlash2) | W4A16 added 2026-10-09; current | Artifact publisher’s vLLM 0.28-only load/evaluation record. | Supports a bounded vLLM canary, not a quality rank or SGLang claim. |
| [SharkWipf Swift 1.5 EXL3](https://huggingface.co/SharkWipf/Swift-1.5-Qwen3.8-Flash-Next-exl3) | 2026-09-25; recent | Author report with Max-Q-adjacent PLE-in-RAM measurements. | Capacity lead; derivative/quant quality remains unproven. |
| [anoane DeepSeek adaptive precision](https://huggingface.co/anoane/DeepSeek-V4-Flash-0731-vllm-moet-sm120-cache) | 2026-07/08; historical | Experimental selective-precision artifact with higher-power measurements. | Do not assume lossless offload or local fit. |
| [Nate Codes configuration](https://x.com/Nateemerson/status/2109484238680494193) | 2026-10-11; current | Public configuration lead on two higher-power GPUs/large RAM. | No one-card fit inference. |
| [Qwen Sharp templates](https://huggingface.co/peculiar-ragdoll/Qwen-Sharp-Chat-Templates) | 2026-10-07–11; current | Public template/harness intervention. | Test templates separately from quant changes. |
| [Ash Hart TensorFold](https://github.com/ashhart/TensorFold) + [public profile](https://x.com/ashxhart) | Observed 2026-10-13; current | Runtime/model qualification matrix; current README is the authority. | Check the exact release/model platform, not older anecdotes. |
| [Elliot Arledge kernel evaluation](https://x.com/elliotarledge/status/2109282873610244181) | 2026-10-11; current | Public kernel microbenchmark lead. | Never relabel as end-to-end model/task evidence. |
| [GLM recipe-fix discussion](https://x.com/UrbanAstroFella/status/2108285677280653619) | 2026-10-08; current | Public unverified report of context/recipe regression. | Inspect code and exact runtime before use. |
| [NVFP4 article lead](https://x.com/Bizuayeu/status/2108937740125605955) | 2026-10-10; current | Public lead; methodology not retained. | Discovery only. |
| [daily development report](https://x.com/satgeze/status/2109174466035249497) | 2026-10-10; current | Qualitative public coding experience with modified weights. | Discovery only; no controlled quality result. |
| [Strata-to-Signal report](https://research.strata2signal.com/flash-next-on-three-cards/) + [original Reddit post](https://www.reddit.com/r/LocalLLM/comments/1x46wxm/benchmarked_strata_0125_and_0139_against_llamacpp/) | 2026-10-12; current | Firsthand close-power RTX PRO report with disclosed engine/drafting confounds. | Runtime/placement lead, not engine-only or quality proof. |
| [hyudryu Strata report](https://www.reddit.com/r/LocalLLaMA/comments/1x2bbx6/strata_with_qwen38_flash_next_udq4_k_xl/) | 2026-10-10; current | Short-output public usage report; SSD-resident PLE clarification. | Discovery/placement lead only. |
| [vera_dev shared Qwen FP8 traffic](https://www.reddit.com/r/LocalLLM/comments/1wo5552/qwen_38_27b_fp8_on_one_rtx_pro_6000_2b_tokens_of/) | Indexed 2026-09-23; observed 2026-10-13; recent | Self-reported sustained coding traffic without task-success denominator or image pin. | Operational lead; no quality/capacity certification. |

## Additional current artifact and runtime leads

| Source | Published / observed; age | Evidence type and relevance | Decision impact |
|---|---|---|---|
| [brandonmusic GLM tr3 4bpw](https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw) + [runtime publisher](https://github.com/brandonmmusic-max/glm-5.3-flash-exl3-4bpw) | Published date unknown; observed 2026-10-13 | Incumbent-quant/runtime publisher; current r19/v84 card identifies the same OCI family. | External 600 W/overclock DFlash figures do not transfer to 300 W Max-Q APC/no-spec; no new-weights claim. |
| [NInfer upstream](https://github.com/Neroued/ninfer), [ninfer-all](https://github.com/iamwavecut/ninfer-all), [ninfer6000](https://github.com/lkarlslund/ninfer6000), and [WaveCut Q2_0 v3](https://huggingface.co/WaveCut/Qwen3.8-Flash-Next-GSQ-RCO-Q2_0-NInfer-v3) | Published dates unknown; observed 2026-10-13 | Upstream, forks, and artifact lead for Flash-Next. Both compared configs record 512 experts/10 active. | Pruning claim removed; no matched RAM bound or native TP2 evidence. Two-device PP is not TP. |
| [Ignis](https://github.com/gpillon/ignis) + [Trellis a25 artifact](https://huggingface.co/gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis) | Published date unknown; observed 2026-10-13 | Maintainer and artifact lead for one-GPU streamed Trellis. | Pre-local external prior: no SWE equivalence or local fit result was established by these sources. Subsequent local R4 terminal SWE is 2/5 overall (two graded/resolved, three empty); see the fixed-five table. Image metadata alone is a pin-discovery lead. |

## Quantization publishers and methods

| Source | Published / observed; age | Evidence type and relevance | Decision impact |
|---|---|---|---|
| [Local Inference Lab GLM QAD FP8 branch](https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD/tree/fp8) | New branch 2026-10-12; current | Artifact publisher; mixed attention/expert precision. GPU quality validation absent. | Pin artifact; require runtime and quality proof. |
| [Unsloth](https://github.com/unslothai/unsloth) | Active 2026-10-13; current | Conversion/distribution maintainer. | Check exact model/export/runtime. |
| [NVIDIA Model Optimizer](https://github.com/NVIDIA/Model-Optimizer) | Active 2026-10-13; current | Vendor quantization/calibration authority. | Format contract, not local quality proof. |
| [vLLM llm-compressor](https://github.com/vllm-project/llm-compressor) | Updated 2026-10-09; current | vLLM compression/export maintainer. | Match exact architecture and runtime. |
| [bartowski Qwen GGUF](https://huggingface.co/bartowski/Qwen3.8-27B-GGUF) | Card 2026-09-20; recent | Artifact distributor. | Metadata/options only. |
| [Ubergarm Qwen GGUF](https://huggingface.co/ubergarm/Qwen3.8-27B-GGUF) | Card 2026-08-23; older | Artifact distributor. | Exact-runtime check required. |
| [ISTA-DASLab GSQ-RCO](https://huggingface.co/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF) | 2026-09-29; recent | Distinct quant method/artifact; expert-pruned derivative. | Separate model arm. |
| [Intel AutoRound](https://github.com/intel/auto-round) | Observed 2026-10-13; current | Quantization method/implementation. | Method lead, no workload win. |
| [HQQ](https://github.com/dropbox/hqq) | Last push 2026-02; historical | Alternate quant method. | Research context only. |
| [MIT Han Lab AWQ](https://github.com/mit-han-lab/llm-awq) | Last push 2025-07; historical | Original activation-aware method. | Historical format context. |
| [AutoAWQ](https://github.com/casper-hansen/AutoAWQ) | Archived 2025-05; historical | Legacy implementation. | Do not create a new operational path. |
| [s-batman MiniMax REAP](https://huggingface.co/s-batman/m51Lab-MiniMax-M2.7-REAP-139B-A10B-NVFP4-GGUF) | 2026-06-01; historical | Pruning plus quant artifact. | Altered model; unmatched quality tests. |

## Runtime, kernels, and hardware authorities

| Source | Published / observed; age | Evidence type and SM120 relevance | Decision impact |
|---|---|---|---|
| [NVIDIA CUDA GPU table](https://developer.nvidia.com/cuda/gpus) + [compute-capability guide](https://docs.nvidia.com/cuda/cuda-programming-guide/05-appendices/compute-capabilities.html) | Observed 2026-10-13; current | Official hardware architecture/reference. | Establish platform class before transferring “Blackwell” claims. |
| [FlashInfer releases](https://github.com/flashinfer-ai/flashinfer/releases) | v0.7.1 2026-10-09; current | Upstream kernel runtime release notes. | Confirm selected image actually includes/dispatches the fix. |
| [vLLM releases](https://github.com/vllm-project/vllm/releases) | Observed 2026-10-13; current | Primary serving runtime releases. | Exact image/model/quant support gate. |
| [SGLang releases](https://github.com/sgl-project/sglang/releases) | Observed 2026-10-13; current | Primary serving runtime releases. | Exact image/model/quant support gate. |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) + [ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) | Active 2026-10-13; current | Upstream and fork for GGUF/hybrid placement. | Pin build; source support is not acceptance. |
| [TabbyAPI](https://github.com/theroyallab/tabbyAPI) | Rolling; observed 2026-10-13 | ExLlamaV3 serving project. | No automatic production-parity claim. |
| [CUTLASS Blackwell guide](https://docs.nvidia.com/cutlass/latest/media/docs/cpp/blackwell_functionality.html) | Observed 2026-10-13; current | NVIDIA kernel-layout authority; distinguishes SM100/SM120. | Avoid generic Blackwell claims. |
| [FlashAttention](https://github.com/Dao-AILab/flash-attention) | Observed 2026-10-13; current | Attention kernel source. | Verify packaged wheel and dispatch. |
| [TensorRT-LLM quantization matrix](https://nvidia.github.io/TensorRT-LLM/latest/features/quantization.html) | Updated 2026-09-26; recent | Vendor compatibility matrix. | Exact quant/platform boundary, not a recipe guarantee. |
| [TileLang](https://github.com/tile-ai/tilelang) | Observed 2026-10-13; current | Kernel DSL/platform source. | Candidate only after profiling. |
| [DeepGEMM](https://github.com/deepseek-ai/DeepGEMM) | Observed 2026-10-13; current | Model-kernel dependency source. | Check SM120 gaps per engine. |
| [Local Inference Lab FlashInfer fork](https://github.com/local-inference-lab/flashinfer) | Observed 2026-10-13; current | Experimental fork. | Fork and released package are separate states. |
| [CAKE tracker](https://github.com/flashinfer-ai/flashinfer/issues/4254) | Date unknown; observed 2026-10-13 | Kernel-research issue lead. | No performance recommendation without body/evidence. |
| [Niko1221 Strata](https://github.com/Niko1221/Strata) | Observed 2026-10-13; current | Qwen-specific GGUF runtime/placement project. | Pin exact release/artifact and run correctness tests. |

## Official releases, benchmark methods, and practical diagnostics

| Source | Published / observed; age | Evidence type and relevance | Decision impact |
|---|---|---|---|
| [Qwen feed](https://huggingface.co/api/models?author=Qwen&sort=lastModified&direction=-1&limit=10) + [DeepSeek feed](https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=10) | Observed 2026-10-13; current | Official identity/lineage inventories. | A mutable feed is not a revision pin. |
| [Z.ai](https://huggingface.co/zai-org/models), [Xiaomi MiMo](https://huggingface.co/XiaomiMiMo/models), [MiniMax](https://huggingface.co/api/models?author=MiniMaxAI&sort=lastModified&direction=-1&limit=10) | Observed 2026-10-13; current | Official variant inventories. | Use to verify base lineage. |
| [Artificial Analysis](https://artificialanalysis.ai/) + [methodology](https://artificialanalysis.ai/methodology/capability-indices) | Observed 2026-10-13; current | External benchmark methodology/capability priors. | Hosted scores are not local-quant results. |
| [LIL Aiderboard](https://evals.local-inference-lab.ai/aider) | Browser observed 2026-10-13; current | Quant-level code-editing leaderboard. | Check dates/settings; pass-2 is not repository-agent success. |
| [SWE-bench](https://www.swebench.com/) | Observed 2026-10-13; current | Benchmark owner for repository issue repair. | Record split, agent, budget, verifier. |
| [Aider Polyglot](https://aider.chat/docs/leaderboards/) | Observed 2026-10-13; current | Benchmark owner for edit-format/multilanguage signal. | Not terminal-agent evidence. |
| [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | Observed 2026-10-13; current | Benchmark owner/source. | Keep contest window and protocol. |
| [Terminal-Bench](https://github.com/harbor-framework/terminal-bench) | Observed 2026-10-13; current | Benchmark owner for terminal-agent tasks. | Compare full systems including tools/time. |
| [lengtsp Qwen benchmark](https://github.com/lengtsp/qwen3.8-27b-benchmark) | 2026-08-25–26; older | One-card latency/resource worklog. | No transferable repository-quality result. |
| [Ai4Users Qwen NVFP4 diagnosis](https://ai4users.com/Articles/1/Qwen-3.8-Flash-Next-NVFP4%3A-benchmark-results-and-the-broken-thinking-mode) | 2026-10-03/04; current | Firsthand failure traces on vLLM 0.30. | Isolate mechanism; symptoms/hardware differ. |
| [Brain OS quant comparison](https://blog.brainosi.com/en/2026-09-01-qwen38-27b-4bit-8bit-flash-next/) | 2026-09-01; recent | Small controlled quant/speculation checks on MLX. | Adjacent platform, not RTX evidence. |
| [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/) + [r/LocalLLM](https://www.reddit.com/r/LocalLLM/) | Observed 2026-10-13; current | Public discovery/reproduction communities. | Read original posts/replies; votes/screenshots do not validate. |

## Pinned public records referenced by the source-map review

These narrow links are retained because the review used them to check identity,
method, or a specific claim. They do not add independent corroboration to a
publisher or runtime already listed above.

| Pinned record | Date / age | Evidence boundary | Decision impact |
|---|---|---|---|
| [Coder390 INT4 W4A16 load record](https://huggingface.co/nerkyor/Qwen3.8-27B-Coder390-EfficientThink-Opus5.5-GPT6Astra-Grok4.7-DSV4Pro-K3-SFT-RLOO-MTP-DFlash2/blob/bae2cbd736952a21006feca2630f5715016250c2/INT4/W4A16/VLLM_LOAD.json) | Revision date unknown; observed 2026-10-13 | Publisher load metadata; vLLM 0.28-specific. | Runtime selection gate only. |
| [Base Qwen EXL3 branch](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3/tree/55a732e0c4c3d4614bc42b68493bb930d9b02c0a) | Last modified 2026-08-31; older | Frozen artifact identity/bytes. | Separate PLE placement from all-GPU fit. |
| [Swift 4.05 branch](https://huggingface.co/SharkWipf/Swift-1.5-Qwen3.8-Flash-Next-exl3/tree/7bbb89dfc867b0de634df1f6222211ef6f8d1d05) + [5.52 branch](https://huggingface.co/SharkWipf/Swift-1.5-Qwen3.8-Flash-Next-exl3/tree/22143f4086898548b693ddfc01f8af8ef2b6c0dd) | Card published 2026-09-25; recent | Publisher-reported footprint/branch identity. | Tight capacity needs local measurement. |
| [DeepSeek EXL3 2.04 branch](https://huggingface.co/turboderp/DeepSeek-V4-Flash-0731-exl3/tree/2.04bpw) + [pinned repair report](https://github.com/gisenberg/local-model-eval/blob/0ac6602be3477faaf82194a4b7599dc853d64a13/results/DEEPSEEK_V4_FLASH_RTXPRO6000.md) | Report 2026-08-09; older | Author-controlled artifact and firsthand run. | Preserve empty-patch failure data. |
| [MiMo runtime README](https://github.com/SSHdotCodes/mimo-v2.6-flash-pro6000/blob/464bf2676aa9bd106cc9833684c0d993e825c005/README.md) | Last pushed 2026-10-06; current | Maintainer RAM/startup guard. | Capacity is conditional pending current local state. |
| [LIL QAD exact fp8 revision](https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD/tree/ddfe59b827eae13150cc5df13b723c87ff843cc4) | 2026-10-12; current | Artifact delta, not GPU quality validation. | Runtime/quality gap remains. |
| [FlashInfer v0.7.1](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.7.1) | Released 2026-10-09; current | Upstream release with SM120/121 work. | Hardware transfer still limited. |
| [llama.cpp CUDA CMake pin](https://github.com/ggml-org/llama.cpp/blob/9d7d5049804be03fce6575cc9f49c058b3c48788/ggml/src/ggml-cuda/CMakeLists.txt) | Revision date unknown; observed 2026-10-13 | Build capability declaration. | Verify build/image, not source alone. |
| [FlashAttention SM120 source](https://github.com/Dao-AILab/flash-attention) | Observed 2026-10-13; current | Source-path availability. | Packaged wheel/dispatch remains a gate. |

## Use and evidence limits

- Primary maintainers establish the identity and published support surface of a
  model, artifact, runtime, or kernel. They do not independently establish a
  local model-quality result.
- Benchmark owners establish what a benchmark measures and how it is scored.
  They do not make an arbitrary hosted score comparable to a local quant.
- Firsthand reports are bounded by their topology, power, host memory, engine,
  context, concurrency, template, and workload. Publication chains count once.
- Public X/Reddit claims without a reproducible underlying record stay leads.
  They must not be elevated to compatibility, capacity, causality, or quality
  evidence.
