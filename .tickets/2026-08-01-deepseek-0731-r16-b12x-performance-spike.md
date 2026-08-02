# DeepSeek 0731 r16 B12X performance and 128K compatibility spike

## Status

Completed as a no-promotion qualification. The pinned r16 B12X/DSpark lane is
functional through 128K and materially faster than its same-runtime no-spec
control, but it fails the 3 GiB physical-free VRAM gate under local WSL/WDDM.

## Discovery

Zeeksa's 2026-07-31 thread reports DeepSeek-V4-Flash-0731 with DSpark K5 on
two 97,887 MiB RTX PRO 6000 Blackwell SM120 GPUs at a configured 131,072-token
context and approximately 230-250 emitted output tokens/s. The attached client
video reports 506 tokens in 2.13 seconds (237.2 tok/s), but does not publish
whether the timer includes TTFT.

This is not a flag-only change to the current qualified engine. The recipe uses
the local-inference-lab r16 image, B12X sparse MLA and W4A8 NVFP4 MoE paths,
InstantTensor, CUDA graphs, and 0.975 GPU-memory utilization. The screenshot
shows 94,721 of 97,887 MiB used per active GPU, leaving about 3.09 GiB, narrowly
above this campaign's 3 GiB physical reserve.

## Pinned external identity

- Thread: https://x.com/dzeeksa/status/2083324856536613229
- Source repository commit:
  https://github.com/local-inference-lab/blackwell-llm-docker/tree/1dc0198b646d73a2fbb0dbafbb32875417150756
- Compose:
  https://github.com/local-inference-lab/blackwell-llm-docker/blob/1dc0198b646d73a2fbb0dbafbb32875417150756/examples/docker-compose-ds4-v20-r16.yml
- Image tag:
  `voipmonitor/vllm:gilded-gnosis-v20-vllm1e9c9c3-sieec30ff-fi801d57a-cu132-20260731-r16`
- Repository-recorded image digest:
  `sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`
- Independently observed registry digest (2026-08-01):
  `sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`
- vLLM base commit: `30038602b71395f481ef4a6edfe4fcf8551d9c15`
- vLLM result tree: `1e9c9c3475fa30ab48d5639f8882f1e93bb552bf`
- FlashInfer commit: `801d57a08958c13d375ddbb6be3be4808f48a708`
- Model: `deepseek-ai/DeepSeek-V4-Flash-0731`
- Model revision: `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`

## Required translation

Create a separate managed Anvil recipe. Do not replace the preserved
`52113932444ed3b8f2228b2589ef2ff3cedf7ab2` FlashInfer image or its named model
cache. Pin the r16 image by verified registry digest, use named model, JIT, AOT,
and temporary-data volumes, and retain the exact upstream identity and license
caveats.

The translation exposed two managed-recipe gaps and fixes them on this branch:
`recipe.serve.model_env` lets an environment-owned launcher receive the exact
recipe model without a stray positional argument, and
`recipe.serve.named_volumes` supplies validated auxiliary Docker volumes for
JIT/AOT and temporary data. Host paths, duplicate mounts, model-cache shadowing,
and model-environment overrides fail closed.

Translate the external defaults:

- `MODE=dspark`, `BACKEND=b12x-a8`, `TP_SIZE=2`, `DCP_SIZE=1`
- fixed DSpark depth, five draft tokens, probabilistic sampling
- FP8 KV, block size 256, `B12X_MLA_SPARSE`, W4A8 NVFP4 MoE
- `MAX_NUM_SEQS=16`, `MAX_NUM_BATCHED_TOKENS=8192`
- `MAX_MODEL_LEN=131072`, `GPU_MEMORY_UTILIZATION=0.975`
- `LOAD_FORMAT=instanttensor`, `INSTANTTENSOR_BACKEND=BUFFERED`
- V2 runner, async scheduling, chunked prefill, prefix caching
- DeepSeek V4 tokenizer, reasoning parser, tool parser, and automatic tools

## Acceptance

- Registry inspection independently verifies the image digest and platform.
- Managed dry-run proves the exact image, checkpoint revision, TP2 ownership,
  named volumes, port, and entrypoint before either GPU is mutated.
- Startup logs prove B12X sparse MLA, W4A8 NVFP4, DSpark K5/`next_n=6`, V2,
  InstantTensor, and the expected KV allocation.
- The 8K low/high/max functional gates pass before long-context requests.
- Three warmed, single-stream 4K runs use the existing DSpark/control protocol.
- A completed 128K prefill/decode probe records TTFT, prefill, decode, E2E,
  acceptance counters, per-card VRAM, power/clock, and at least 3 GiB free.
- Results are compared with the preserved FlashInfer DSpark lane using identical
  prompts, sampling, output caps, timing boundaries, and aggregation.
- Failure preserves a stopped container for managed logs and restores the normal
  split stack without losing the saved image or named caches.

## Attempt log

### 2026-08-02 - first managed start failed before GPU allocation

The exact image and model snapshot were present and the launcher rendered the
expected B12X/DSpark command. With `require_complete_cache=true`, Anvil added
`HF_HUB_OFFLINE=1`. vLLM localized the primary model argument but left the
repository ID nested in `speculative_config.model`, then rejected that ID while
offline. The failed container exited before GPU allocation. Live
`--preserve-on-failure` proof retained it for managed logs and restored
`omni-stack`.

The recipe now supplies both `MODEL_PATH` and `SPEC_MODEL_PATH` as the exact
verified snapshot directory and omits `MODEL_REVISION` for that local path. This
keeps the primary and DSpark draft identity identical without disabling the
offline completeness contract.

### 2026-08-02 - second managed start reached TP worker initialization

The local-snapshot translation worked: the API server resolved both target and
DSpark draft to revision `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`, built the
131,072-token engine config, and launched both TP ranks. Both workers then failed
inside `ncclCommInitRank` with `NCCL error: unhandled cuda error`, before model
weight loading.

Read-only image inspection identified the incompatible defaults carried by the
pinned r16 image: `NCCL_P2P_LEVEL=SYS` and B12X's default
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`. The upstream Compose file
targets native Linux, runs privileged, and does not translate NCCL for WSL2.
This workstation's earlier TP2 campaign already reproduced the same NCCL
progression: direct PCIe P2P failed communicator setup, NCCL cuMem failed with
CUDA error 999 after P2P was disabled, and expandable segments failed at the
first worker allocation after cuMem was disabled.

The next managed attempt therefore adds the already-proven WSL2 contract:
`NCCL_P2P_DISABLE=1`, `NCCL_CUMEM_ENABLE=0`,
`NCCL_CUMEM_HOST_ENABLE=0`, shared-memory IPC, and
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:False`. `NCCL_DEBUG=INFO` remains
enabled for qualification evidence. This is a platform translation, not a
performance tune; B12X sparse MLA, W4A8 MoE, InstantTensor, DSpark K5, context,
batching, and GPU-memory utilization remain unchanged.

### 2026-08-02 - third managed start reached the 131K KV admission gate

The WSL2 transport translation passed. Both NCCL communicators completed over
shared-memory transport with `cuMemEnable 0`; both ranks loaded the target and
DSpark weights; startup proved V2, B12X FP8 dense kernels, B12X W4A8 FP4 MoE,
DeepSeek FP8 MLA KV, `next_n=6`, and the expected DSpark draft.

The run then failed the 131,072-token admission gate by 0.15 GiB: 7.37 GiB of
KV was required and 7.22 GiB was available. CUDA-graph profiling measured about
0.88 GiB total per rank, of which roughly 0.8 GiB was retained. Raising
`gpu_memory_utilization` enough to hide that reservation would conflict with the
physical 3 GiB reserve gate.

The next attempt keeps `gpu_memory_utilization=0.975`, 131,072 context, K5,
8,192 batched tokens, and every B12X path, but reduces `MAX_NUM_SEQS` from 16 to
8. That changes the reachable DSpark graph cap from 96 to 48 rows for this
single-stream qualification lane and is expected to recover more than the
missing 0.15 GiB without consuming the safety reserve. The external 16-sequence
profile remains a separate concurrency target after the context gate passes.

### 2026-08-02 - fourth start and functional/context qualification passed

Reducing `MAX_NUM_SEQS` to 8 lowered retained graph memory to about
0.52-0.55 GiB per rank. The engine exposed 138,459 KV tokens and admitted the
131,072-token serve. Low, high, and max reasoning preflights passed; the low
gate included smoke, JSON, 3/3 typed tools, streaming tools, tool-result
continuation, and Responses API.

The replacement quality ladder completed 32K, 64K, and a clamped 126,464-token
request with no failures. Actual prompt counts were 23,831, 67,415, and
125,785 tokens. Per-row effective prefill was 1,574, 1,747, and 1,484 tok/s;
time to first output was 15.14, 38.60, and 84.75 seconds; visible TTFT was
19.30, 41.63, and 85.75 seconds; decode was 102.1, 89.0, and 113.4 tok/s.

Three independent 4K/c1 DSpark runs completed 9/9 requests. Median-of-run-p50
decode was 130.7 tok/s and aggregate throughput was 101.7 tok/s. Cumulative
serve counters recorded 4,865 accepted of 8,830 drafted tokens (55.1%), or
2.75 accepted tokens per draft.

### 2026-08-02 - same-image no-spec control isolated DSpark's effect

The control used the exact image, checkpoint, B12X kernels, TP2 transport,
allocator, context, batching, and memory ceiling with `MODE=dspark-mtp0`.
Its full low-reasoning functional gate passed. Three clean 4K/c1 runs produced
a median-of-run-p50 decode rate of 64.9 tok/s and 59.6 aggregate tok/s.
DSpark therefore improved median decode by 101.4%, aggregate throughput by
70.5%, and median end-to-end latency by 58.8%. One additional no-spec run is
retained as reliability evidence because one of three requests exhausted its
2,048-token reasoning budget without emitting visible content.

The no-spec lane exposed 257,515 KV tokens and used 1.6-2.3 GiB less VRAM than
DSpark, but neither lane satisfied the 3 GiB reported-free reserve. Post-run
DSpark snapshots reported 395 and 265 MiB free; no-spec reported 1,982 and
2,593 MiB. WSL/WDDM global memory includes host allocations, so these values
are not directly interchangeable with the external native-Linux screenshot.
The result remains no-promotion rather than silently weakening the gate.

## Caveats

- The report used 600 W RTX PRO 6000 cards; the local cards are Max-Q.
- The external model revision differs from the current auroter NVFP4 checkpoint.
- The completed 128K request proves functionality, but not the 3 GiB reserve.
- The repository has no root license file at the pinned commit. Local evaluation
  is allowed by the current task, but redistribution or a derived public image
  requires component-license review.
