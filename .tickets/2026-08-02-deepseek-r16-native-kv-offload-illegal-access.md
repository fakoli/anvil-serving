# DeepSeek r16 native KV offload crashes on the first long-context request

**Observed:** 2026-08-02
**Status:** Resolved locally; 128K and 256K qualified with guarded lifecycle; no promotion

## Problem

The pinned DeepSeek V4 Flash 0731 r16 TP=2 DSpark serve starts and passes
short smoke and structured-JSON requests with `--kv-offloading-size 8`, but the
first approximately 8K-token preflight request kills the engine with a CUDA
illegal-memory-access error. All subsequent requests return HTTP 500.

This blocks native CPU KV offload qualification on the two RTX PRO 6000
Blackwell Max-Q host. It is not safe to benchmark or increase context until a
bounded reproduction passes.

## Exact candidate

- Image:
  `voipmonitor/vllm@sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`
- Runtime:
  `v0.11.2.dev280+gilded.gnosis.v20.vllm1e9c9c3.sieec30ff.fi801d57a.cu132.20260731.r16`
- Model revision: `9e165c30e2704aec5d9d593cce3eebd58bbef1cb`
- Recipe:
  `configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-offload8-recipe.toml`
- TP=2, DSpark K=5, FP8 DeepSeek KV, max model length 131072,
  `max_num_seqs=8`, chunked prefill 8192, native CPU offload 8 GiB.

## Reproduction

1. Enter the managed exclusive TP=2 target with `--preserve-on-failure`.
2. Confirm startup creates one process-shared
   `/dev/shm/vllm_offload_*.mmap` file of 8.59 GB and reports the native
   `OffloadingConnector` / `CPUOffloadingSpec`.
3. Run the independent preflight with smoke, JSON, an 8192-token needle, tool,
   and Responses checks at low reasoning effort.
4. Smoke and JSON pass. The needle request returns HTTP 500; the engine dies;
   remaining checks also return HTTP 500.

Evidence:

- `docs/findings/2026-08-02-deepseek-0731-native-kv-offload-evidence/preflight-low.json`
- Preserved container:
  `vllm-tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-offload8-128k`

The first actionable worker failure is:

```text
torch.AcceleratorError: CUDA error: an illegal memory access was encountered
```

The reported Python frame is `block_table.py:138 -> buffer_utils.py:257 ->
async_tensor_h2d`, but CUDA errors are asynchronous. Shutdown also fails while
synchronizing the native offload store transfer event, so the exact offending
kernel is not yet proven.

## Cleanup defect observed

The engine exited after health had passed, leaving the 8.59 GB
`/dev/shm/vllm_offload_*.mmap` file behind. No vLLM/Python serving process owns
it, but it must not be removed until container/process ownership is checked.
The failed container remains retained for diagnosis as requested.

## Diagnostic result: global WSL pin-memory disable is not viable

A second managed candidate kept the exact image, model, TP=2, DSpark K=5,
128K configuration, and 8 GiB offload pool, but set
`VLLM_WSL2_ENABLE_PIN_MEMORY=0` and `CUDA_LAUNCH_BLOCKING=1`. Both workers
failed during V2-runner initialization before the model or offload connector
could load:

```text
vllm/v1/worker/gpu/states.py -> StagedWriteTensor -> UvaBuffer
RuntimeError: UVA is not available
```

The managed `--preserve-on-failure` transaction retained the exited container,
then restored the starting split topology and the `omni` owner on
`dark-compute-b`. This isolates an important WSL2 constraint: the global pin
memory override cannot be disabled merely to avoid `cudaHostRegister` for the
shared offload mmap, because the V2 runner's normal staged-write buffers also
depend on UVA.

Diagnostic recipe:

- `configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-offload8-wsl-unpinned-recipe.toml`
- Preserved container:
  `vllm-tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-offload8-wsl-unpinned-128k`

The next bounded run keeps `VLLM_WSL2_ENABLE_PIN_MEMORY=1` and adds only
`CUDA_LAUNCH_BLOCKING=1`, with sequential smoke, JSON, and 8K needle checks.
If that attributes the fault to the native offload mmap transfer, the durable
engine change should separate shared-mmap registration from the global V2 UVA
setting rather than disabling pin memory globally.

## Synchronous attribution

The mmap-only hypothesis reproduced with `VLLM_WSL2_ENABLE_PIN_MEMORY=1` and
only `CUDA_LAUNCH_BLOCKING=1` added. Smoke and structured JSON both returned
valid responses, but immediately after the first short request both TP ranks
reported:

```text
CachingHostAllocator.cpp:26 Warning: Exception in pinned allocator free()
terminate called after throwing an instance of 'c10::AcceleratorError'
CUDA error: an illegal memory access was encountered
```

The independent 8K needle then received HTTP 500 because the workers were
already dead. This moves the first actionable failure from an asynchronous V2
staged-write frame to PyTorch's pinned host allocator lifetime and confirms
that the broad 8K request is not the originating model-kernel failure.

Evidence:

- `docs/findings/2026-08-02-deepseek-0731-native-kv-offload-evidence/preflight-sync-low.json`
- Diagnostic recipe:
  `configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-offload8-sync-recipe.toml`

The pinned derived-image experiment lives under
`configs/runtime-patches/vllm/48518e91-wsl2-offload-mmap-unpinned/`. It adds a
dedicated `VLLM_KV_OFFLOAD_MMAP_PIN_MEMORY` switch. Setting it to `0` skips only
`cudaHostRegister` for the process-shared mmap; the global WSL2 pin-memory
override remains enabled for V2 UVA and the small descriptor buffers. The
existing engine warning already states that unregistered mmap transfers remain
functional but may be slower, which must be validated by real store/replay
evidence before this translation is accepted.

## Required resolution

- Isolate whether the failure requires native offload, TP=2, DSpark,
  asynchronous scheduling, or concurrent preflight requests.
- Add a bounded product-managed replay/offload probe so a failed candidate is
  never sent a broad concurrent tool batch after the first engine-fatal error.
- Make managed teardown detect and safely report orphan offload mmap files;
  cleanup must verify no live process or retained running container references
  the engine ID before removal.
- Re-run short, 8K, repeated-prefix store/replay, and cleanup gates before any
  throughput or 128K/256K claim.

## Validated WSL2 translation

The pinned derived image
`anvil-vllm@sha256:331b79259b9532788b44f13696d484a0d1d576231c6ad397c0f0faf72b85cd86`
passed the bounded 128K qualification with
`VLLM_KV_OFFLOAD_MMAP_PIN_MEMORY=0` and
`VLLM_WSL2_ENABLE_PIN_MEMORY=1`:

- short smoke, structured JSON, 8K retrieval, tool calling, streaming tools,
  tool-result recovery, and Responses checks passed;
- low, high, and max reasoning controls passed without mixing modes;
- cold context probes passed through 123,964 input tokens;
- the native connector stored 7,147,768,320 bytes GPU-to-CPU and replayed
  1,130,976,000 bytes CPU-to-GPU during the session;
- replay of a 52,495-token prefix reached 99.5% cache hits with 0.514-second
  time to first output;
- DSpark accepted 6,990 of 11,065 draft tokens (63.2%);
- the matched 4K/c1 decode median was 123.7 tokens/s versus 130.7 tokens/s
  without offload, a 5.36% reduction; aggregate throughput was 99.0 versus
  101.7 tokens/s, a 2.62% reduction;
- managed shutdown returned the host to split mode, restored `omni` on
  `dark-compute-b`, and unlinked the 8.59 GB offload mmap; host reclaim reduced
  page cache from 48.7 GB to 23.5 GB.

The new environment switch is consumed by the patched runtime but is not in
the base vLLM environment registry, so startup emits an `Unknown vLLM
environment variable` warning. This is a diagnostic/polish follow-up, not a
claim that the switch was ignored: both TP workers explicitly log the skipped
registration and the store/replay counters prove the unregistered mmap path is
active.

The first managed leave restored the correct topology but the client command
timed out at 124 seconds while waiting for the restore-group transaction. Mode
status was authoritative and showed the completed split/Omni state. The CLI
should distinguish a completed transition whose client wait expired from an
actual rollback failure.

## 256K first attempt: stale mmap exhaustion

The first 256K escalation preserved the exact validated derived image and
offload size, changed `MAX_MODEL_LEN` to 262144, and reduced
`MAX_NUM_BATCHED_TOKENS` to 4096. GPU profiling succeeded and reported a
506,401-token GPU KV cache, but both workers failed while constructing the
shared offload region:

```text
Created mmap file /dev/shm/vllm_offload_12c3bfe8-25cc-4114-8b91-bf160413b6ec.mmap (8.59 GB)
Opened existing mmap file /dev/shm/vllm_offload_12c3bfe8-25cc-4114-8b91-bf160413b6ec.mmap
shared_offload_region.py:100, in __init__
    self.mmap_obj.madvise(...)
OSError: [Errno 14] Bad address
```

Host inspection then found four orphan `vllm_offload_*.mmap` files: three
8 GiB files from earlier runs and the new partially populated 7.1 GiB file.
Together they filled 31.1 of the 31.4 GiB host tmpfs. No live process mapped
any of the four exact paths; three corresponding containers no longer existed,
and the new failure container was preserved but exited. Removing only those
four verified-orphan mmap files recovered `/dev/shm` to 19 MiB used and 31.4
GiB available. No `psm_*` files were removed. A subsequent host reclaim reduced
page cache from 25.0 GB to 0.9 GB.

This is evidence that lifecycle cleanup must be a product preflight for native
offload: page-cache reclaim alone does not remove orphan tmpfs mappings. The
same exact 256K recipe must be retried with clean `/dev/shm` before changing any
model or batching setting.

## 256K qualification and product resolution

The identical 256K recipe started successfully after exact orphan cleanup. Its
cold ladder passed at 92,754, 193,064, and 249,573 actual prompt tokens. The
250K row measured 43.75-second time to first output, 45.58-second
first-visible TTFT, 5,705 effective prefill tokens/s, 135.2 decode tokens/s,
and 45.85-second end-to-end latency. The session stored 11,057,713,920 bytes
GPU-to-CPU. Cached-prefix follow-ups passed, but their external-offload hit
counter remained zero; these were correctly retained as GPU-cache hits.

A follow-up measured 503,468 GPU KV tokens and confirmed that the original
8 GiB CPU tier was smaller than the GPU tier at the observed bytes per token.
The otherwise identical 16 GiB recipe reported 506,283 GPU KV tokens and
created a 17,177,796,608-byte mmap. Six sequential distinct 150K planned-context
requests raised prefix queries to 682,044 and stored 13,627,722,240 bytes to
CPU. Replaying exact request zero increased external hits by 113,408 and
CPU-to-GPU bytes by 1,001,721,600 in 0.344 seconds; GPU prefix hits were
unchanged. The replay passed with visible output, 0.825-second TTFO,
1.974-second visible TTFT, and 2.192-second end-to-end latency. This closes the
256K CPU-to-GPU reload gap without disabling GPU prefix caching.

Anvil Serving now owns the mmap lifecycle:

- `host shared-memory status` and controller `host_shared_memory` report exact
  mmap paths, sizes, live mappings, active native-offload containers, and
  reclaim eligibility;
- `host shared-memory reclaim --confirm` and controller `host_manage` action
  `reclaim-shared-memory` perform two matching inspections, remove only exact
  verified-orphan paths, and check the postcondition;
- native-offload recipe load fails closed if ownership cannot be established
  and reclaims verified orphans before Docker starts;
- recipe unload and manifest teardown reclaim after the owning container stops
  or is removed, including a stopped container preserved for logs.

The final live regression entered the 256K target in 234 seconds. While it was
running, the inspector saw one 8,587,821,056-byte mmap mapped by both TP workers
and correctly blocked cleanup. Full preflight passed. Managed leave took 166.7
seconds, force-removed the target, reclaimed the exact mmap, restored split
mode with `dark-compute-a` free and `omni` on `dark-compute-b`, and reduced WSL
page cache from 9.6 to 0.9 GB. The final shared-memory inspection reported zero
files.

The 16 GiB lifecycle regression also passed: managed leave took 170.2 seconds,
reclaimed the exact 17,177,796,608-byte mmap, reduced WSL page cache from 17.7
to 0.9 GB, restored `omni` on `dark-compute-b`, left `dark-compute-a` free, and
reported zero remaining offload mmap files.

Publication:

- `docs/findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md`
- `docs/findings/2026-08-02-deepseek-0731-native-kv-offload-evidence/lifecycle-256k-regression.json`
- `docs/findings/2026-08-02-deepseek-0731-native-kv-offload-evidence/cpu-gpu-reload-256k-offload16-counters.json`
