# Independent prelaunch gate

Reviewer: mimo_sol_gate, explicitly spawned gpt-5.6-sol with high reasoning and no inherited history. Runtime has no self-attestation API; parent spawn metadata is identity evidence. Author: GPT-6 Astra.

Verdict: PASS WITH GATES. Reviewed recipe SHA-256: adf465b5d530102eadcd82b365b468d11c065042d66b1845d5a15f3b940ce5d6.

Pinned model and immutable SGLang image verified; recipe validation passes.52GiB RAM, zero swap,12GiB reserve rendered correctly, with in-container cgroup guard. Host IPC ignores shm-size but45GiB host shared-memory capacity is adequate at observation.

Before load require post-GLM-unload MemAvailable >=65536MiB; managed load must refuse otherwise. After load require containment-passed, exact image/model identity, health and direct preflight. Restore exact GLM and prove authenticated routed smoke and readmission. No concrete recipe blocker remains if admission gate passes. This is authorization for no promotion; user campaign authority remains unchanged.

Sol follow-up identified required --trust-remote-code: SGLang0.5.20 does not register mimo_v2 config in its HF config registry, and Transformers5.12.1 lacks the mapping. Added flag for exact pinned complete local snapshot; built-in runtime model class alone does not satisfy AutoConfig. No arbitrary remote revision is used.

## V2 initialization-OOM diagnosis

Verdict: BLOCK the V2 recipe; permit one source-supported successor using `--moe-runner-backend flashinfer_mxfp4` and no other backend exploration.

The pinned SGLang v0.5.20 MiMo override leaves `moe_runner_backend=auto` on SM120. The FP8 loader recognizes `store_dtype=mxfp4` and allocates packed 4-bit expert weights, but the ordinary `Fp8MoEMethod` defaults MXFP4 block scales to FP32. The SM120 FlashInfer MXFP4 wrapper instead supplies `fp4_scale_dtype=torch.float8_e8m0fnu`, preserving the checkpoint's one-byte scales and using its supported W4A8 path.

MiMo has 48 transformer layers with the first layer dense, so the scale-expansion estimate uses 47 routed-expert layers, not 48. From the retained config, the inferred expert-weight count is `47 * 256 * 3 * 4096 * 2048 = 302,795,194,368`; at one scale per 32 weights, widening each scale from one to four bytes adds `28,387,049,472` bytes, or about 26.44 GiB across TP2. That closely matches the observed approximate allocation gap of about 27 GiB. This is a source-and-config inference, not precise allocator attribution or profiler measurement. The OOM at `AudioProjection` was the final failed 128 MiB allocation after the expanded model allocation, not evidence that audio alone consumed the gap.

Pinned sources:

- MiMo backend override: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/arg_groups/model_overrides/mimo_v2.py#L28-L36
- FP4 expert allocation and default FP32 scales: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/layers/quantization/fp8.py#L1227-L1381
- Native E8M0 SM120 wrapper: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/layers/quantization/mxfp4_flashinfer_cutlass_moe.py#L48-L83
- Unconditional paired vision/audio construction: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/models/mimo_v2.py#L1229-L1246

No v0.5.20 launch flag disables audio while preserving text and vision. `--language-only` still constructs both encoders for local fallback, and `--language-model-only` does not support MiMo. Context reduction cannot remedy this pre-KV initialization failure.

## Final correctness review

Verdict: FAIL QUALIFICATION. Retain GLM and do not promote MiMo.

The third recipe loaded the exact pinned checkpoint through SGLang v0.5.20 and reached endpoint health. Weight loading completed at 81.41 GiB per rank, with no missing-parameter warnings. All 48 deferred fused-QKV scale tensors resolved on both ranks. These facts establish successful runtime initialization and transport health; they do not establish correct inference.

Correctness failed twice. The matched temperature-zero preflight exhausted 9,216 completion tokens in reasoning with no visible answer. A separate request using the model card's recommended `temperature=1.0` and `top_p=0.95` also exhausted 9,216 tokens, producing incoherent multilingual output entirely in `reasoning_content` and no visible answer. The second result rules out treating the first failure as merely long coherent reasoning or an unsuitable deterministic sampling setting. Parser behavior can classify an unclosed thinking stream as reasoning, but it cannot explain the incoherent decoded tokens.

The root cause is unknown. The pinned runtime deliberately supports loading the checkpoint's TP4-interleaved fused QKV weights at TP2 by grouping two checkpoint shards per rank, then dequantizing, de-interleaving Q/K/V, and requantizing their block scales. That non-reference and lossy transform is a plausible suspect surface, but the retained logs show that it completed with expected shapes on both ranks. The explicit SM120 `flashinfer_mxfp4` W4A8 expert path is another plausible implementation surface because no independent same-checkpoint control isolated it, but the pinned source explicitly supports SM120 and startup prepared all 47 routed-expert layers. There is no evidence sufficient to attribute the malformed output to either resharding or the kernel.

The remaining startup warnings do not identify a cause. The launch-command and RoPE messages are deprecations. Custom all-reduce setup failed while parsing UUID-form `CUDA_VISIBLE_DEVICES`, after which SGLang used its fallback communication path; no retained evidence ties that fallback to corrupted output. The Transformers `top_p` warning reflects validation of the checkpoint generation configuration and does not prove that the explicit OpenAI request field was ignored. The official checkpoint chat template was selected, while `mimo` reasoning and tool parsers were explicitly configured as required by the model card.

The recipe configured and advertised a 327,680-token context, but the measured full-attention KV pool held 91,342 tokens at the default 0.8 SWA ratio. Therefore 327,680 is a configured limit, not demonstrated capacity. The approximately 128 output tokens per second observed while generating malformed output is not valid MiMo throughput and must not be used as benchmark or comparison evidence.

Pinned sources:

- Official checkpoint deployment and sampling guidance: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/blob/3b38d063180c3e4aed9691fdc735f3d10b266ee4/README.md
- Official checkpoint chat template: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/blob/3b38d063180c3e4aed9691fdc735f3d10b266ee4/tokenizer_config.json
- Official checkpoint generation configuration: https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL/blob/3b38d063180c3e4aed9691fdc735f3d10b266ee4/generation_config.json
- Pinned SGLang MiMo TP reshard and deferred-scale implementation: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/models/mimo_v2.py#L103-L285
- Pinned SGLang MiMo weight-loading integration: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/models/mimo_v2.py#L1570-L1680
- Pinned SGLang SM120 FlashInfer MXFP4 implementation: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/layers/quantization/mxfp4_flashinfer_cutlass_moe.py#L33-L256
- Pinned SGLang hybrid-SWA pool sizing: https://github.com/sgl-project/sglang/blob/v0.5.20/python/sglang/srt/model_executor/pool_configurator.py#L552-L797

The three-start campaign exhausted its bounded configuration budget without a correct MiMo response. Keep the restored GLM deployment, make no promotion, and record MiMo v2.6 Flash on this SGLang v0.5.20 TP2/SM120 recipe as loadable but correctness-unqualified.
