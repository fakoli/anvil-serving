# DeepSeek V4 Flash 0731 native KV offload and 256K qualification

**Capture date:** 2026-08-02<br>
**Decision:** priority intelligence `challenger`, `no-promotion`<br>
**Measured hardware:** 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive
TP=2 over PCIe without NVLink<br>
**Evidence:** `functional` and `capacity`; lifecycle regression passed

## Outcome

The pinned DeepSeek V4 Flash 0731 r16/B12X/DSpark lane now has a working
262,144-token configuration on this WSL2 host. The 256K serve completed cold
125K, 192K, and 250K context requests. A separate 16 GiB offload run then
evicted a 113,408-token prefix from the 506,283-token GPU KV tier and reloaded
1,001,721,600 bytes from CPU to GPU. A fresh managed enter also passed the full
functional preflight, and managed leaves removed both 8 and 16 GiB
native-offload mmaps before restoring the normal split topology.

The first 256K start failed with `OSError: [Errno 14] Bad address`, but the
model and kernel recipe were not the cause. Four orphan
`/dev/shm/vllm_offload_*.mmap` files occupied 31.1 of the 31.4 GiB tmpfs.
Removing only files with no live process mapping and no running native-offload
container made the identical recipe start successfully. Anvil Serving now
performs that ownership check before a native-offload recipe starts and after
its managed container stops.

This result does not promote the model. The 131K DSpark lane remains the
preferred performance recipe because it has the stronger repeated quality,
matched no-spec, and per-card reserve evidence. The 256K lane establishes a
useful coding-agent context ceiling and a safe lifecycle, not a new production
alias.

## Immutable identity and recipe

| Component | Pinned value |
|---|---|
| Model | `deepseek-ai/DeepSeek-V4-Flash-0731` |
| Model revision | `9e165c30e2704aec5d9d593cce3eebd58bbef1cb` |
| Derived image | `anvil-vllm@sha256:331b79259b9532788b44f13696d484a0d1d576231c6ad397c0f0faf72b85cd86` |
| Base image | `voipmonitor/vllm@sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f` |
| Runtime | pinned r16 B12X, vLLM base `30038602b71395f481ef4a6edfe4fcf8551d9c15` |
| Quantization | official mixed FP8/FP4 checkpoint; B12X W4A8 NVFP4 MoE, FP8 dense GEMM, FP8 MLA KV |
| Parallelism | TP=2, exclusive ownership, PCIe without NVLink |
| Context/admission | 262,144 served tokens, eight admitted sequences, 4,096 batched-token cap; c1 measured |
| Speculation | DSpark fixed depth, five draft tokens |
| Native offload | 8 GiB capacity lane; 16 GiB CPU-reload lane; process-shared mmap |

The exact 8 GiB context recipe is
[`configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload8-wsl2-mmap-unpinned-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload8-wsl2-mmap-unpinned-recipe.toml).
The counter-backed reload uses the otherwise identical
[`configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload16-wsl2-mmap-unpinned-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload16-wsl2-mmap-unpinned-recipe.toml),
whose larger CPU tier exceeds the measured GPU KV capacity.
The derived runtime keeps `VLLM_WSL2_ENABLE_PIN_MEMORY=1` for V2 UVA, while
`VLLM_KV_OFFLOAD_MMAP_PIN_MEMORY=0` skips CUDA host registration only for the
process-shared offload mmap. Its patch and build receipt are retained under
[`configs/runtime-patches/vllm/48518e91-wsl2-offload-mmap-unpinned/`](https://github.com/fakoli/anvil-serving/tree/main/configs/runtime-patches/vllm/48518e91-wsl2-offload-mmap-unpinned).

## Functional and context results

The retained low-reasoning preflight passed smoke, structured JSON, 8K
retrieval, tools, streaming tools, tool-result continuation, and Responses.
The final lifecycle regression independently passed smoke, JSON, a roughly
128K needle, and a 20/20 shared-prefix tool batch.

| Target | Actual prompt | Cached | TTFO | First-visible TTFT | Effective prefill | Decode | E2E |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 125K | 92,754 | 0 | 14.10 s | 18.52 s | 6,579 tok/s | 133.8 tok/s | 18.60 s |
| 192K | 193,064 | 4,096 | 30.77 s | 31.90 s | 6,275 tok/s | 126.5 tok/s | 32.14 s |
| 250K | 249,573 | 5,632 | 43.75 s | 45.58 s | 5,705 tok/s | 135.2 tok/s | 45.85 s |

The 8 GiB session recorded 11,057,713,920 bytes stored GPU-to-CPU and DSpark
accepted 1,203 of 2,320 draft tokens, or 51.9%. The 192K and oldest-125K
follow-ups reported 139,264 and 92,672 cached prompt tokens, with 1.13-second
and 0.57-second TTFO. Those two hits remained GPU prefix-cache evidence: their
external-offload hit counter was zero.

The 16 GiB qualification made the CPU tier larger than the measured
506,283-token GPU KV tier. Six sequential, distinct 150K planned-context
requests raised prefix queries to 682,044 and stored 13,627,722,240 bytes to
CPU. Recreating request zero then passed with 113,674 prompt tokens and 99.8%
cache reuse. GPU prefix hits did not move, while external hits increased from
zero to 113,408 and CPU-to-GPU transfer increased from zero to 1,001,721,600
bytes in 0.344 seconds. The replay measured 0.825-second TTFO, 1.974-second
visible TTFT, 2.192-second end-to-end latency, 137,856 effective prefill tok/s,
and 117.7 tok/s decode. This is the 256K-configured CPU-to-GPU reload proof.

The context artifact uses the benchmark envelope as a diagnostic carrier and
has empty quality suites; its reported validation errors do not invalidate the
per-request capacity measurements above. It is not quality-ranking evidence.

## Failure attribution and lifecycle fix

The initial 256K failure occurred after GPU profiling reported capacity for
506,401 KV tokens. Both workers then created/opened the offload mmap and failed
at `mmap.madvise` with `Bad address`. Inspection found three complete 8 GiB
orphans and one partial 7.1 GiB orphan, none mapped by a live process. Page
cache reclaim did not affect these tmpfs files.

The durable Anvil Serving lifecycle now:

1. scans only `/dev/shm/vllm_offload_*.mmap` and validates every returned path;
2. checks `/proc/*/maps` for live mappings and Docker configuration for a
   running native-offload owner;
3. fails closed when either ownership source is unavailable;
4. requires two matching inspections before deleting exact paths; and
5. verifies the files are absent afterward.

`anvil-serving host shared-memory status` and the controller's
`host_shared_memory` tool are read-only. `host shared-memory reclaim --confirm`
and controller `host_manage` action `reclaim-shared-memory` apply the guarded
cleanup. Native-offload recipe load/unload and manifest-owned teardown invoke
the same postcondition automatically. Preserved stopped failure containers
keep their logs while their unmapped tmpfs allocation is reclaimed.

The live regression entered exclusive TP=2 in 234 seconds. While running, the
inspector saw one 8,587,821,056-byte mmap mapped by both TP workers and refused
reclamation. Managed leave took 166.7 seconds, removed the container,
reclaimed that exact mmap, reduced WSL page cache from 9.6 to 0.9 GB, restored
`omni` on `dark-compute-b`, and left `dark-compute-a` free. A final inspection
reported zero offload mmap files.

## Decision and caveats

- `challenger`, `no-promotion`; no router alias or production recommendation
  changed.
- The 256K ladder is single-request capacity evidence, not concurrency proof.
- The six-request eviction setup was sequential and is not a throughput run.
  Four requests emitted visible answers; two exhausted their reasoning budget
  without visible text. The subsequent exact replay independently passed the
  visible-answer gate and is the reload measurement.
- Per-card free-VRAM telemetry was not sampled for the 256K ladder. The 128K
  lane already fails the standing 3 GiB reported-free reserve, so the larger
  lane cannot satisfy the promotion gate by inference.
- The derived patch is narrowly qualified on this WSL2/runtime/GPU tuple. It
  must not be generalized to another vLLM, CUDA, GPU, dtype, TP size, or model
  geometry without requalification.
- The runtime warns that the new environment variable is outside the base
  vLLM registry. Worker logs prove the switch is consumed, but upstream polish
  remains useful.

## Raw evidence

- [256K low-reasoning preflight](2026-08-02-deepseek-0731-native-kv-offload-evidence/preflight-256k-retry1-low.json)
- [Cold 125K/192K/250K ladder](2026-08-02-deepseek-0731-native-kv-offload-evidence/context-ladder-256k-low.json)
- [192K cached-prefix follow-up](2026-08-02-deepseek-0731-native-kv-offload-evidence/context-192k-replay-256k-low.json)
- [Oldest-125K cached-prefix follow-up](2026-08-02-deepseek-0731-native-kv-offload-evidence/context-125k-oldest-replay-256k-low.json)
- [8 GiB 70K cold control](2026-08-02-deepseek-0731-native-kv-offload-evidence/prefix-sequence-256k-70k.json)
- [8 GiB 80K prefix control](2026-08-02-deepseek-0731-native-kv-offload-evidence/prefix-sequence-256k-80k.json)
- [8 GiB 100K prefix control](2026-08-02-deepseek-0731-native-kv-offload-evidence/prefix-sequence-256k-100k.json)
- [8 GiB 70K GPU-cache replay control](2026-08-02-deepseek-0731-native-kv-offload-evidence/prefix-sequence-256k-70k-replay-offload8.json)
- [16 GiB eviction sequence](2026-08-02-deepseek-0731-native-kv-offload-evidence/eviction-sequence-256k-offload16-6x150k.json)
- [16 GiB exact prompt replay](2026-08-02-deepseek-0731-native-kv-offload-evidence/replay-256k-offload16-prompt0-150k.json)
- [256K CPU-to-GPU counter delta and restoration](2026-08-02-deepseek-0731-native-kv-offload-evidence/cpu-gpu-reload-256k-offload16-counters.json)
- [Initial stale-tmpfs failure](2026-08-02-deepseek-0731-native-kv-offload-evidence/startup-256k-stale-shm-error.txt)
- [Final managed lifecycle regression](2026-08-02-deepseek-0731-native-kv-offload-evidence/lifecycle-256k-regression.json)

All public artifacts are below the per-file and per-finding evidence limits.
