# Dual RTX PRO 6000 TP2 compatibility brief

Observed 2026-08-01. This is a pre-launch decision record, not benchmark evidence.
Every measured claim must be replaced or corroborated by a raw local artifact before
the dated finding describes a model as passing.

## Topology boundary

- Hardware: two NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition GPUs,
  96 GB each, connected over PCIe without NVLink.
- Runtime: Docker Desktop on WSL2. Current NVIDIA WSL guidance still documents
  multi-GPU container-selection limitations, so local NCCL initialization is an
  explicit hard gate rather than an assumed capability.
- Mode: exclusive TP2. All non-campaign inference serves must be offline while a
  candidate owns both GPU roles.
- Baseline: one request at a time, bounded context, no speculative decoding, and
  deterministic output-coherence checks before performance timing.
- Distributed transport: disable direct NCCL P2P and retain shared-memory fallback.
  The first Qwen TP2 attempt reached both ranks but failed NCCL communicator setup
  when the recipe forced P2P across the PCIe topology.
- NCCL allocation: disable automatic device and host `cuMem` allocation. With P2P
  already disabled, the diagnostic Qwen run failed identically on both ranks at
  `ncclCuMemMapAndSetAccess` with CUDA error 999 under WSL2.
- PyTorch allocation: use the native default allocator, not the experimental
  expandable-segment mode. After NCCL cuMem was disabled, the next failure moved to
  the first worker `torch.zeros()` allocation while that virtual-memory allocator
  remained enabled.
- Safety: model aliases are not promoted by this campaign. A failed launch is
  unloaded through the managed recipe surface before the next candidate starts.

## Launch decisions

| Candidate | Exact revision | First engine lane | Quantization interpretation | Pre-launch decision |
|---|---|---|---|---|
| DeepSeek V4 Flash 0731 | `7872f01b1d1fe23eabc4c98b48bffcef5a386062` | current pinned SGLang, TP2 | Publisher hybrid: FP4 MoE experts plus FP8 quantization | Use the official checkpoint. No genuine NVFP8-labeled artifact was found; new NVFP4 conversions are larger and low-evidence. Start with MXFP4 MoE, bounded context, and no speculative decode. |
| Inkling Small NVFP4 | `b6a99534467840620d411e4cd4ad5819b2610d9c` | current Inkling-capable SGLang, TP2 | Native NVFP4 | Use Marlin MoE plus Triton attention. CUTLASS is excluded from the baseline because same-hardware reports describe silent repeated-output corruption. Fail closed on deterministic answer and repetition checks. |
| Nemotron 3 Super 120B A12B NVFP4 | `4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6` | pinned NVIDIA vLLM, TP2 plus expert parallel | Native NVFP4 | Reuse the exact previously qualified checkpoint, adopt the current official expert-parallel baseline, keep MTP disabled, and compare against the dated TP1 result. |
| Qwen3.5 122B A10B NVFP4 | `98915d837c4e7c87ac8296d02e89de19b3207e6d` | pinned NVIDIA vLLM, TP2 | Native ModelOpt NVFP4 | Preserve the proven TP1 controls, change only the distributed topology where possible, and keep MTP disabled. |
| Laguna S 2.1 NVFP4 | `07614121b31898586430f189d27a25a0be310843` | pinned vLLM 0.25.x, TP2 | Native NVFP4 | Establish a matched baseline without DFlash. Speculative decoding is a separate optional lane only after the TP2 baseline passes. |

## DeepSeek quantization search result

The exact publisher checkpoint already declares `expert_dtype=fp4` and
`quant_method=fp8`. As of the observation date, the catalog exposed ordinary FP8
derivatives and two very new NVFP4 conversions, but no model using an NVFP8 format
or label. `NVFP8` must therefore not be used as the campaign's quantization name.
The official hybrid checkpoint is both the most authoritative artifact and smaller
than the observed third-party NVFP4 conversions.

## Inkling-Small quantization search result

The July 30 release publishes BF16 and native NVFP4 checkpoints. The current
quantization catalog also exposes an ordinary dynamic FP8 conversion with about
266B safetensor parameters, but no artifact using an `NVFP8` format or label.
That FP8 conversion exceeds the workstation's 192 GB aggregate VRAM before KV
and runtime allocation. The publisher NVFP4 checkpoint is therefore the only
native Blackwell quantization selected for local TP2.

SGLang's current command-generator source marks a two-DGX-Spark Marlin/Triton
TP2 cell as verified. The local baseline follows its ModelOpt FP4, page-size,
unified-radix, and Mamba-state controls, while preserving the WSL2 transport
workarounds already proven in this campaign. Its balanced BF16 KV path is the
correctness baseline; MXFP8 KV is explicitly a separate long-context lane.

## Evidence gates

For every candidate, capture the resolved model SHA, image digest, engine version,
GPU topology, launch flags, startup logs, model-list response, deterministic known
answer, structured-tool behavior, context/capacity result, and performance samples.
A server that becomes healthy but emits corrupt or repetitive output fails the
functional gate and is not performance-benchmarked.
