# CUDA Green Context and MPS capability milestone

**Point-in-time record, 2026-07-12.** Sources were accessed on 2026-07-12. This finding records
upstream capability evidence, the repository extension points selected for a read-only inspector,
and the first proposed mutating experiment. No Green Context, MPS daemon, MPS partition, MIG mode,
serve, model, or routing configuration was created or changed for this milestone.

## Executive conclusion

CUDA Green Contexts are an officially documented, same-process mechanism for assigning specific
SMs and optionally work queues to streams created for a lightweight execution context. CUDA MPS
static partitioning is an officially documented, cross-process mechanism for assigning exclusive
SM chunks to MPS clients on Ampere-or-newer GPUs. Neither mechanism partitions VRAM, memory
controllers, L2 cache, or the physical CUDA device identity. MIG is the mechanism that can provide
hardware memory-system partitions and separate device identities on supported products.

The RTX 5090 is architecturally eligible for the documented SM mechanisms (`sm_120`, approximately
170 enabled SMs), but **support on Fakoli Dark's Windows 11 -> WSL2 -> Docker Desktop path is not
proven**. Driver 610.62 and the operator-reported hardware are inputs, not successful API evidence.
The new inspector can prove visibility of versions, symbols, tools, UUID ownership, and a running
MPS daemon's read-only command surface. It deliberately cannot prove resource creation.

The completed read-only smoke covered three distinct layers. Native Windows observed both expected
GPU UUIDs and names, driver 610.62, CUDA UMD compatibility 13.3, CUDA Toolkit 12.8, compute
capability 12.0, and `cuGreenCtxCreate` in the driver. Ubuntu WSL2 loaded CUDA Runtime 13.2
(`cudaRuntimeGetVersion=13020`) and exposed every required Runtime and Driver Green Context symbol.
The `docker-desktop` WSL distro exposed `/dev/dxg`, the Windows-driver `libcuda.so` stub, and running
`dockerd`/`containerd`; as expected for that minimal VM it did not itself contain `nvcc`, `libcudart`,
`nvidia-smi`, or MPS. Those user-space components came from the digest-pinned official NVIDIA CUDA
13.1.1 devel image. The subsequent one-shot Compose probe returned `mutated_state=false`, created no
CUDA context, and launched no workload. Raw evidence:
[docker-desktop-rtx5090-prerequisite.json](2026-07-12-green-context-mps-capability-evidence/docker-desktop-rtx5090-prerequisite.json).

Current milestone verdicts:

- RTX 5090 Green Context: **API prerequisites proven inside the actual Docker Desktop CUDA 13.1
  container path; Green Context resource creation remains unknown** pending a separate
  confirmation-gated experiment. The container saw exactly the configured RTX 5090 UUID, compute
  capability 12.0, 170 SMs, Runtime 13.1, Driver 13.3, and all required Runtime/Driver symbols.
- MPS static partitioning: **blocked on native Windows; unknown under WSL2/Docker Desktop**. Current NVIDIA WSL documentation
  does not explicitly establish this new MPS mode on that path; absence of a control binary is not
  evidence that the GPU architecture itself is unsupported.
- Production use: **no-go**. Capability creation, cleanup, interference, and latency protection have
  not been measured.

## Chosen repository extension points

The implementation extends repository-native seams instead of creating an operational script:

| Concern | Extension point | Reason |
|---|---|---|
| Public command | `anvil_serving.command_tree` -> `host gpu-sharing inspect` | Keeps help, policy metadata, topology resolution, and the generated command manifest authoritative. |
| GPU discovery | `anvil_serving.gpus.list_gpus` | Reuses bounded `nvidia-smi` inventory and stable UUID identity. |
| Role binding | `anvil_serving.gpus.resolve_gpu_roles` plus `topology.load_topology` | Attaches declared roles by UUID; runtime indexes remain observations, never stable topology identity. |
| Capability logic | `anvil_serving.gpu_sharing` | Keeps optional CUDA/framework probing out of the router hot path and preserves the stdlib-only runtime. |
| Output | Versioned JSON with `operation=gpu_sharing_inspect` and `mutated_state=false` | Makes degraded and ambiguous results machine-readable without inventing a parallel lifecycle surface. |
| Later experiment | Existing `examples/fakoli-dark/docker-compose.experiment.yml` project | Preserves Compose as source of truth and the distinct `fakoli-experiment` project boundary. |

The command is local/native in this milestone. A remote controller tool can be added later if a
real split-host operator need justifies broadening the control-plane contract.

## Environment and version evidence

Operator-provided Fakoli Dark context (not independently reconfigured by this work):

| Item | Reported value | Evidentiary limit |
|---|---|---|
| Host path | Windows 11, WSL2 Ubuntu, Docker Desktop | Green Context/MPS creation not tested. |
| Driver | NVIDIA Studio 610.62 | Driver presence alone does not prove the WSL/container path. |
| Fast GPU | GeForce RTX 5090, GB202, `sm_120`, ~170 SMs, 32 GB | Target for the later probe; UUID must come from a private topology overlay. |
| Heavy GPU | RTX PRO 6000 Blackwell Max-Q, GB202, `sm_120`, 188 SMs, 96 GB ECC | Not a target for this experiment. |
| Assignment | UUID plus `CUDA_DEVICE_ORDER=PCI_BUS_ID` | Required by the repository's Docker Desktop/WSL2 convention. |

Read-only command evidence from this milestone:

| Inspector field | Observed value |
|---|---|
| GPU 0 | RTX 5090, UUID `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1`, compute capability 12.0 |
| GPU 1 | RTX PRO 6000 Blackwell Max-Q, UUID `GPU-d0f446cf-1771-414c-e116-a39138798a8c`, compute capability 12.0 |
| Driver / CUDA UMD | 610.62 / 13.3 |
| Host toolkit | CUDA 12.8 |
| Green Context symbols | Driver `cuGreenCtxCreate=true`; Runtime `cudaGreenCtxCreate=unknown` |
| Native MPS | control binary unavailable; native-Windows status `blocked_by_environment` |
| SM count | unavailable from this driver's `nvidia-smi` query surface; operator values remain reported inputs, not inspector proof |

WSL and Docker Desktop substrate evidence:

| Layer | Observed value |
|---|---|
| Ubuntu WSL2 | Ubuntu 24.04.4, WSL kernel 5.15.167.4, driver 610.62 / CUDA UMD 13.3 |
| Ubuntu CUDA Runtime | 13.2 (`13020`), `libcudart.so.13` |
| Runtime symbols | `cudaGreenCtxCreate`, `cudaDevSmResourceSplitByCount`, `cudaDeviceGetDevResource`, `cudaDevResourceGenerateDesc`, `cudaExecutionCtxStreamCreate`, and `cudaExecutionCtxGetDevResource` all present |
| Driver symbols | `cuGreenCtxCreate`, `cuDevSmResourceSplitByCount`, `cuDeviceGetDevResource`, `cuDevResourceGenerateDesc`, `cuGreenCtxStreamCreate`, and `cuGreenCtxRecordEvent` all present |
| `docker-desktop` | `/dev/dxg` and WSL `libcuda.so` present; `dockerd` and `containerd` running |
| Docker engine | `desktop-linux`, Docker 29.5.3 |
| Probe image | Official NVIDIA CUDA 13.1.1 devel, pinned index/image identity `sha256:9cf8694a...14d32`, linux/amd64 |
| Docker probe | Pass; UUID `GPU-04d3b6e7...40cf1`, RTX 5090, sm_120, 170 SMs, Runtime 13.1 / Driver 13.3, all Green Context prerequisite symbols present |

The inspector reports driver version, driver-reported CUDA compatibility, `nvcc` toolkit version,
host runtime symbol visibility, whether it is itself inside a container, Docker engine visibility,
and why container-runtime visibility remains unknown when no container was entered. It does not
copy these observations into `operator-topology.toml`.

## Upstream research record

Classification uses: **officially documented**, **merged but experimental**, **proposed or proof of
concept**, **unsupported**, and **unknown**. An issue or open pull request is never promoted to
supported.

| Capability/source | Version or status on access | Classification | Conclusion / decision impact |
|---|---|---|---|
| [NVIDIA CUDA Green Contexts](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/green-contexts.html) | Current CUDA Programming Guide; archived [CUDA 13.1 guide](https://docs.nvidia.com/cuda/archive/13.1.0/cuda-programming-guide/04-special-topics/green-contexts.html) retained for the experiment baseline | Officially documented | Green Contexts provision SM and work-queue resources for streams in one application. Use a CUDA 13.1-or-newer Runtime API probe for the first `sm_120` experiment. |
| [NVIDIA CUDA 13.1.1 devel image](https://hub.docker.com/layers/nvidia/cuda/13.1.1-devel-ubuntu24.04/images/sha256%3Ad947d5877524350576b77998ee0c05fe81306baaa56f5ad8e955a305dffac12d) | Tag exists; multi-platform index digest `sha256:9cf8694a27722418a1f175d90f85d5afb5a728fd4a9907d7f0565efecfa14d32` on access | Officially published image | Suitable build baseline. Resolve and record the linux/amd64 digest immediately before the approved experiment rather than assuming an index digest selects one architecture. |
| [CUDA 13.3 driver compatibility table](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html) | CUDA 13.3 GA corresponds to Linux driver 610.43.02 or newer; Windows toolkit driver is listed N/A because CUDA 13.1+ no longer bundles it | Officially documented | Installed Windows Studio driver 610.62 already exposes CUDA UMD 13.3 to WSL. No driver update is required for this probe. |
| [CUDA Driver Green Context API](https://docs.nvidia.com/cuda/cuda-driver-api/group__CUDA__GREEN__CONTEXTS.html) | Current | Officially documented | `cuGreenCtxCreate` can return `CUDA_ERROR_NOT_SUPPORTED`; symbol presence is necessary evidence, not a successful capability test. |
| [NVIDIA MPS overview](https://docs.nvidia.com/deploy/mps/latest/index.html) and [static SM tasks](https://docs.nvidia.com/deploy/mps/appendix-common-tasks.html) | MPS r610 documentation | Officially documented | Static mode uses `-S`; `sm_partition add/rm` mutate state, while `lspart` is read-only. Ampere+ is the documented hardware floor and Hopper+ dGPU chunks are normally eight SMs. |
| [MPS tools reference](https://docs.nvidia.com/deploy/mps/610/appendix-tools-and-interface-reference.html) | r610 | Officially documented | The inspector may query the control binary and `lspart`; it must never send `quit`, `start_server`, or `sm_partition add/rm`. |
| [CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html) | Current guide accessed 2026-07-12 | Unknown for these mechanisms | The guide documents CUDA and container constraints but does not provide direct Green Context or static-MPS proof for this topology. Keep WSL results `unknown` until a bounded experiment succeeds. |
| [PyTorch `GreenContext`](https://docs.pytorch.org/docs/stable/generated/torch.cuda.green_contexts.GreenContext.html) | PyTorch 2.12 documentation; API marked beta | Merged but experimental | Optional higher-level path exists, but beta API/package presence is not the first proof target. |
| [FlashInfer green context API](https://docs.flashinfer.ai/api/green_ctx.html) | FlashInfer 0.6.15 docs | Merged but experimental | Useful helper after the CUDA Runtime baseline; package discovery alone does not prove it works on `sm_120`/WSL2. |
| [SGLang PD-Multiplexing issue #10813](https://github.com/sgl-project/sglang/issues/10813) and [current server arguments](https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/server_arguments.md) | Issue closed/inactive; `--enable-pdmux` is documented, with issue checklist gaps | Merged but experimental | Do not change the validated serve. Revisit only after the primitive probe and interference gate. |
| [TensorRT-LLM PR #9020](https://github.com/NVIDIA/TensorRT-LLM/pull/9020) | Open PR on access | Proposed or proof of concept | Reported B200 results are a research prior, not supported Fakoli Dark functionality. |
| [vLLM issue #30211](https://github.com/vllm-project/vllm/issues/30211) | Closed as not planned | Unsupported | The cited multi-stream/Green Context experiment is not a supported vLLM path. No vLLM change is planned here. |
| [NVIDIA MIG supported GPUs](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-gpus.html) | Current | Officially documented | RTX PRO 6000 Blackwell variants are listed; GeForce RTX 5090 is not. MIG remains out of scope and must not be enabled or changed. |

Repository/upstream searches for `green_ctx`, `PD multiplexing`, `static SM partitioning`,
`CUDA_MPS_SM_PARTITION`, `workqueue green context`, `WSL MPS`, and `sm_120 green context` produced
the sources above. No current primary source found in this pass directly proves Green Context or
static MPS creation on GeForce RTX 5090 through WSL2 and Docker Desktop.

## Green Context vs MPS vs MIG

| Property | Green Context | MPS static partition | MIG |
|---|---|---|---|
| Process boundary | Cooperating streams/contexts in one process | Independent CUDA client processes through MPS | Independent device instances |
| SM allocation | Specific SM resources; optional work queues | Exclusive SM chunks | Hardware compute slices |
| VRAM capacity partition | No | No; capacity remains shared | Yes, by supported profile |
| L2 / memory-controller partition | No documented partition | No; caches/bandwidth remain shared | Yes, isolated paths/slices on supported profiles |
| CUDA device identity | Same physical device | Same physical device | Separate MIG device identities |
| Current RTX 5090 conclusion | Architecturally plausible, unproven here | Ampere+ hardware rule passes, WSL path unproven | Not listed as a supported MIG product |
| Mutation boundary | Context/resource creation | Daemon start and partition add/rm | GPU mode and instance changes; explicitly prohibited here |

The Green Context and MPS mechanisms can reserve compute scheduling resources, but they do not by
themselves guarantee TTFT/TPOT isolation when workloads still contend for memory bandwidth, cache,
copy engines, power, thermals, or VRAM.

## What the inspector proves

`anvil-serving host gpu-sharing inspect` can prove, at inspection time:

- observed GPU UUID/name/index and extended compute-capability/SM-count fields when `nvidia-smi`
  exposes them;
- deterministic binding of declared topology roles to those UUIDs;
- driver/toolkit version evidence and Green Context symbol visibility without calling the symbol;
- Python package discoverability for PyTorch and FlashInfer, clearly labelled as package evidence;
- MPS control-binary presence, whether a control daemon answers `get_server_list`, and whether the
  read-only `lspart` command is recognized by an already-running daemon;
- every subprocess timeout, malformed row, permission failure, or missing tool as structured data.

It cannot prove that a Green Context can be created, that returned SM sets are disjoint, that MPS
can start in static mode, that a partition can be created, that Docker passes the needed API through,
or that either mechanism improves latency. It starts no container to fill the container-runtime
field. The command's source contains no MPS mutator input and its unit tests assert the invoked
command set.

The separate, profile-gated `gpu-sharing-inspect` Compose validation harness proves the exact
Docker Desktop/image/GPU path and SM count. It is now wrapped by the durable, confirmation-gated
`anvil-serving host gpu-sharing probe` operator surface. The wrapper renders and audits the service
before every run, pins the requested UUID through `FAST_GPU_UUID`, and refuses weakened container
safety, image drift, command drift, writable/moved source binds, and probe-source hash drift. Direct
Compose invocation remains a diagnostic fallback.

## Safety limits

The implementation never calls a CUDA context-creation function and never sends MPS `quit`,
`start_server`, `sm_partition add`, or `sm_partition rm`. It does not call `nvidia-smi` mode setters,
reset a GPU, inspect credentials, kill a process, start a daemon, run a model, or write runtime facts
into topology. All subprocesses are bounded (default 10 seconds; maximum 60 seconds), and degraded
evidence returns a successful structured inspection rather than an uncaught host-tool traceback.

## First experiment: specified, not executed

The read-only prerequisite container is implemented and proven as the profile-gated
`gpu-sharing-inspect` service in the existing `fakoli-experiment` Compose project, and the guarded
`anvil-serving host gpu-sharing probe` wrapper now owns repeat runs. A later, separately approved
change may extend the minimal CUDA Runtime API C++ binary for a creation experiment. The C++ path remains preferred
over PyTorch or FlashInfer because it isolates the platform primitive from beta framework wrappers,
Python package compatibility, and serving-engine state.

Planned immutable inputs:

- image: `nvidia/cuda:13.1.1-devel-ubuntu24.04` pinned to its resolved digest when implemented;
- GPU: private topology role for the RTX 5090, resolved to its full `GPU-...` UUID;
- environment: `CUDA_DEVICE_ORDER=PCI_BUS_ID` and `CUDA_VISIBLE_DEVICES=<resolved UUID>`;
- Compose: `examples/fakoli-dark/docker-compose.experiment.yml`, project `fakoli-experiment`,
  `restart: "no"`, no published port, no production service replacement;
- artifact directory: `.anvil/evidence/gpu-sharing/<UTC-run-id>/`, containing source revision,
  image digest, compiler/runtime/driver versions, topology snapshot identity, GPU UUID, stdout JSON,
  stderr, exit code, and cleanup result.

Implemented prerequisite command (preview first; live execution requires confirmation):

```powershell
anvil-serving host gpu-sharing probe `
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml `
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 `
  --dry-run

anvil-serving host gpu-sharing probe `
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml `
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 `
  --confirm
```

This product verb stops at the same non-creating prerequisite inspection already proven in the raw
artifact. It does not implement the future Green Context resource-creation result shape below.

Expected structured result shape:

```json
{
  "schema_version": 1,
  "operation": "green_context_probe",
  "mutated_state": true,
  "cleanup_complete": true,
  "gpu_uuid": "GPU-...",
  "runtime_version": "13.1",
  "resources": {
    "total_sms": 170,
    "alignment": 0,
    "partitions": []
  },
  "checks": [],
  "warnings": []
}
```

The future command must require `--confirm` before Compose creates a container or CUDA creates a
context. It must compile first, then create two non-overlapping SM partitions, create/bind streams,
run only a short deterministic kernel, verify resources through the Runtime API, destroy streams and
contexts in-process, and use Compose removal only for the probe-owned container. It must preserve any
pre-existing MPS state and refuse to clean resources it did not create.

## Go/no-go criteria

Go to the first experiment only when all are true:

1. `host gpu-sharing inspect` binds the private `fast` role to the observed RTX 5090 UUID.
2. Driver compatibility and the pinned CUDA 13.1+ runtime are not mismatched.
3. Both `cudaGreenCtxCreate` and the required resource-query/split symbols are present in the probe
   image before creation is attempted.
4. The production Fast serve and other RTX 5090 workloads have an approved maintenance window; the
   probe does not stop or replace them itself.
5. The exact image digest, artifact path, cleanup contract, and operator confirmation are recorded.

Additional Fakoli Dark safety gate: the RTX 5090 is the primary Windows display GPU. The successful
prerequisite probe performed only driver/runtime queries. Any context creation, kernel launch, MPS
operation, sustained load, or interference benchmark requires an explicit maintenance window,
display-health observation, and rollback plan. The experiment must stop immediately on display
instability, driver recovery/TDR, or loss of the Windows desktop.

No-go on UUID mismatch, missing/old symbols, unpinned image, existing unowned probe container,
ambiguous cleanup, or any need to reset the GPU/change MIG or compute mode. A successful creation is
still only a capability result; production work remains blocked until repeated cleanup and an
interference benchmark pass.

## Deferred roadmap

The proven Compose recipe is now in the Anvil Serving CLI; an MCP/controller wrapper remains a
split-host follow-up. After a separately approved successful primitive creation probe: (1) repeated creation/cleanup evidence, (2) sequential vs normal
streams vs Green Context interference measurement, (3) optional native-Linux MPS static-partition
validation if WSL support is independently established, and only then (4) serving-engine PD
multiplexing research. GPU leases, scheduling, eviction, routing policy, model promotion, and
production partitioning remain explicitly deferred.
