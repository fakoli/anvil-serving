# GLM-5.3-Flash K3/DFlash2 dual-PRO 1M optimization and promotion

**Date:** 2026-08-30

**Scope:** two RTX PRO 6000 Blackwell Max-Q cards, WSL2/Docker Desktop,
exclusive TP=2/DCP=2, text/tools/image/OCR, 1,048,576 configured tokens,
K3/K5 DFlash2 and scheduler-chunk A/B

**Decision:** `current`; the K5/2,048-token configuration replaced the earlier
262K GLM hands-on profile as the human-authorized one-week default. K3 remains
a verified high-concurrency alternate. The 4,096-token scheduler-chunk trial
is rejected.

<!-- benchmark-result-card/v1 -->
## Result card

> The exact `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` checkpoint with the
> DFlash2 K5 draft became the local GLM default on two RTX PRO 6000 Max-Q
> cards after passing 1M-capacity, text/tool, image/OCR, bounded quality,
> routed, and real-client gates.

| Setup | Qualified value |
|---|---|
| Model | `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4`; served as `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-1m-vision` |
| Hardware | 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB each, sm_120, TP=2/DCP=2 over PCIe without NVLink |
| Runtime | `ghcr.io/tpurtell/glm-5.3-flash-exl3-4bpw-2x-rtx@sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75`; source `d46fdeddf8c6fec2d4595b65535a32d80a5af787`; vLLM `0.1.dev20051+g487ecf187` |
| Quantization | EXL3/MCG K3 routed experts with native attention/shared/vision/MTP tensors; FP8 DS-MLA target KV; BF16 DFlash2 K5 draft KV |
| Recipe | [managed K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml) |
| Measurement path | warm direct and authenticated routed OpenAI-compatible APIs under Docker Desktop/WSL2; real Hermes, OpenClaw, and Pi acceptance |
| Contract | 1,048,576 context, 8,192 output, maxseq16, router c16, up to 16 images, no video; locally proven KV pool 2,917,371 tokens |
| Evidence | `functional`, `capacity`, bounded `quality`, `performance`, concurrency, routed/client acceptance; campaign complete |
| Decision | `current`; live router and Mini client catalogs changed under the explicit promotion request |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| 4K c1 decode | 82.1 tok/s | K5, five requests; 1.00 s median TTFT |
| 131K c1 decode | 67.4 tok/s | K5, five requests; 19.17 s median TTFT |
| 240K c1 decode | 67.9 tok/s | K5, five requests; 38.15 s median TTFT |
| Near-maximum retrieval | exact needle at a 950K target | one request, 242.0 s |
| Functional/capability | tools 20/20; image corpus 12/12; bounded quality 12/12 | direct K5; routed post-promotion subset also all-pass |
| Retained negative | K5/batch4096 fell to 62.5 tok/s at 4K | matched five-request c1 trial; no C16 benefit |

**Why it matters:** this profile raises the locally advertised GLM context from
262,144 to 1,048,576 tokens, retains image/OCR, and materially improves long
single-request latency over the earlier local GLM profile. It also leaves
enough measured KV capacity for two complete configured windows, rather than
merely forcing a one-window launch.

**Important caveat:** this is a community target, draft, and custom runtime.
The DFlash2 draft is CC-BY-NC-ND-4.0 and is therefore an evaluation/noncommercial
component unless separate permission is obtained. Video is not supported.
Router c16 is a short-request scheduling ceiling, not proof of sixteen
simultaneous 1M requests.

Evidence manifest:
[raw artifacts](2026-08-29-glm53-k3-dflash2-optimization-evidence/README.md) ·
Publication summary:
[derivative copy](2026-08-29-glm53-k3-dflash2-optimization-evidence/publication-summary.md)

## Outcome and decision

K5 with `--max-num-batched-tokens 2048` is the selected default. It passed the
complete direct preflight, exact 950K-target retrieval, the six-case image-only
corpus twice, repeated deterministic intelligence/session/tool checks, and
authenticated routed acceptance. Four retained Hermes profiles then passed
real text plus auxiliary-image turns, OpenClaw passed its running-gateway
dynamic image path, and Pi passed its normal extension-loaded PTY image path.
Router decision metadata recorded only successful
`llm.primary`/`llm.secondary`/`vision.general -> primary-local` outcomes during
the final acceptance buffer.

K3 passed the bounded functional suite and two C16 repetitions. It is retained
as a verified alternate because its C16 aggregate results, 36 and 42 output
tok/s, exceeded K5's repeat at 26 and overlapped its first run at 35. K5 remains
the default because its 4K c1 decode was 82.1 tok/s versus K3's 66.8.

Raising the scheduler chunk from 2,048 to 4,096 tokens did not resolve the
runtime's underfilled-draft-slot warning into an end-to-end win. It reduced 4K
c1 decode to 62.5 tok/s and produced 32 aggregate output tok/s at C16, so the
trial is rejected and the durable recipe returned to 2,048.

## Exact configuration

- Target checkpoint:
  `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4`
- Source BF16 recorded by the publisher:
  `zai-org/GLM-5.3-Flash@f12e0fe0d085f38ac964cf1a5ff1caa79a4d0928`
- Draft checkpoint:
  `incoai/GLM-5.3-Flash-DFlash2@dc77ff1c99eeb2df044ee3d4f0094eb033fee410`
- Runtime image index digest:
  `sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75`
- Linux/amd64 manifest:
  `sha256:ea2ba10c826ec1efaf97776a157195f5b7ec41dec18fe7592c5a3edaf9980a2c`
- Runtime source:
  `tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx@d46fdeddf8c6fec2d4595b65535a32d80a5af787`
- Context/maxseq/batch: 1,048,576 / 16 / 2,048
- Speculation: DFlash2 fixed K5, BF16 draft KV
- Target KV: `fp8_ds_mla`
- Media: text plus up to 16 images; video limit zero
- GPU memory utilization: 0.95

The later Hugging Face `main` snapshot observed during the campaign changed
repository storage metadata rather than the selected model/config payload.
The qualification remains pinned to `319d66a...` so the tested bytes cannot
drift silently.

The native-Linux recipe's CUDA-IPC custom PCIe all-reduce, B12X DCP A2A, and
top-k owner exchange failed under WSL2 with `peer access is not supported`.
The local recipe disables those three paths plus vLLM custom all-reduce and
uses PyNCCL. The EXL3 K3 kernels, sparse B12X MLA, FP8 DS-MLA target KV, and
DFlash2 draft remain active. This WSL2 translation is a local configuration,
not a claim that the upstream native-Linux recipe needs it.

The engine reported 16.61 GiB of KV allocation per card and 2,917,371 KV
tokens, or 2.78 complete configured windows. It also suggested a larger fixed
KV allocation, but the qualified 0.95 utilization profile retains operating
reserve and already passed c2 at a 500K target; the larger allocation was not
promoted without an A/B.

No missing MoE/GEMM configuration warning appeared, so this campaign did not
invent or publish a kernel-tune artifact. First-request JIT warnings are
retained as warm-up latency observations, not as evidence that a tune would
improve end-to-end performance.

## Research path and candidate selection

| Source | Observed | Evidence class | Decision impact |
|---|---|---|---|
| [Purtell dual-RTX repository](https://github.com/tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx/tree/d46fdeddf8c6fec2d4595b65535a32d80a5af787) | 2026-08-29 | community recipe/runtime prior | selected K3 target, DFlash2 K5, FP8 target KV, 1M/c16/image profile for local translation |
| [Cardillo dual-PRO repository](https://github.com/samuelcardillo/glm-5.3-flash-2x-rtx-pro-6000-blackwell) | 2026-08-29 | hardware-matched community prior | established the earlier 262K local starting point and validation checklist |
| [Exact K3 target](https://huggingface.co/wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1/tree/319d66a8b53092b491f698440ecea781e4ddd4e4) | 2026-08-29 | community checkpoint | selected target; complete local cache verified at 137,095,744,155 bytes |
| [DFlash2 draft](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2/tree/dc77ff1c99eeb2df044ee3d4f0094eb033fee410) | 2026-08-29 | community draft | selected fixed-K5 draft; license boundary retained |
| [0xSero 3.0-bpw](https://huggingface.co/0xSero/GLM-5.3-Flash-EXL3-3.0bpw) | 2026-08-29 | community checkpoint/watch item | rejected from execution because no supported dual-GPU server path was published and the publisher retained a quality-gate failure |
| [vLLM EXL3 issue](https://github.com/vllm-project/vllm/issues/53963) | 2026-08-29 | upstream compatibility context | confirmed that this is a custom-runtime path, not portable stock-vLLM support |
| [LocalLLaMA release megathread](https://www.reddit.com/r/LocalLLaMA/comments/1vyzzxu/megathread_glm53flash_former_oxalpha/) | 2026-08-29 | practitioner discussion | used only to discover candidate leads; no local result was inferred from comments |

The upstream non-Max-Q/native-Linux report publishes much higher short decode
and C16 aggregate rates. Those numbers remain external priors because this host
uses Max-Q cards and WSL2 without the upstream CUDA-IPC collectives.

## Method

The incumbent and challenger used the same two physical cards, exclusive TP=2,
the same online capacity harness, and low/default reasoning controls unless an
artifact says otherwise. Single-request depth sweeps used five requests at 4K,
32K, 65K, 131K, and 240K; the K5 500K point used three. Short concurrency used
C2/C4/C8/C16, with 8/12/16/32 total requests. Long concurrency used C2 at
131K, 240K, and, for K5, 500K. Short aggregate output rate is sensitive to the
model's variable response length and is therefore a capacity diagnostic, not a
pure fixed-token throughput benchmark.

The functional gate covered smoke, strict JSON, calibrated retrieval, tools
20/20, a long-context tool call, streaming tools, tool-result continuation,
Responses, image understanding, and OCR. The image-only corpus contains six
synthetic cases with two repetitions. Bounded quality used three repetitions
for deterministic intelligence, session, and tool items with
`reasoning_effort=high`; that control was requested but not independently
verified by the server.

## Results

### Matched single-request depth sweep

| Requested context | Incumbent decode | K5 decode | Incumbent TTFT | K5 TTFT |
|---:|---:|---:|---:|---:|
| 4K | 64.5 tok/s | **82.1 tok/s** | 1.08 s | **1.00 s** |
| 32K | 50.5 tok/s | **62.2 tok/s** | 8.19 s | **7.81 s** |
| 65K | 55.2 tok/s | **67.2 tok/s** | 16.60 s | **11.16 s** |
| 131K | 48.0 tok/s | **67.4 tok/s** | 34.39 s | **19.17 s** |
| 240K | 45.7 tok/s | **67.9 tok/s** | 64.32 s | **38.15 s** |
| 500K | not configured | 61.4 tok/s | not configured | 123.36 s |

The incumbent is the previously qualified
`brandonmusic/GLM-5.3-Flash-tr3-4bpw` fixed-K5 vision profile at 262,144
tokens. The K5 challenger improves median 240K TTFT by 40.7% and decode by
48.6% in this matched local comparison.

### Concurrency and capacity

| Profile | Workload | Result |
|---|---|---|
| K5 | C2, 131K target, 4/4 | 54.39 s median TTFT; 55.21 s median E2E |
| K5 | C2, 240K target, 2/2 | 37.41 s median TTFT; 43.76 s median E2E |
| K5 | C2, 500K target, 2/2 | 75.33 s median TTFT; 85.25 s median E2E |
| K5 | C16 short, 32/32, two runs | 35 then 26 aggregate output tok/s |
| K3 | C16 short, 32/32, two runs | 36 then 42 aggregate output tok/s |
| K5/batch4096 | C16 short, 32/32 | 32 aggregate output tok/s |
| K5 | 950K-target retrieval | exact `ZEBRA-42917-QUARTZ` in 242.0 s |

The 950K pass establishes near-ceiling single-request retrieval. The C2 500K
pass plus the reported KV pool establishes two half-window requests. Neither
result proves broad 1M reasoning quality or more than two complete-window
requests.

### Functional, quality, and client acceptance

- Direct full preflight: all checks passed, including tools 20/20, calibrated
  long context, image understanding, and exact OCR.
- Image-only corpus: 12/12.
- Bounded high-reasoning suite: intelligence 6/6, session 3/3, tools 3/3.
- Restored exact K5 subset: all checks passed.
- Authenticated routed subset: all checks passed with exact served identity.
- Hermes: four retained profiles each passed one text and one auxiliary-image
  turn on their selected `llm.primary` or `llm.secondary` alias.
- OpenClaw: running gateway dynamic-image turn passed.
- Pi 0.84.2: normal extension-loaded `ssh -tt` image turn passed.
- Final decision buffer: 60/60 served; 45 `llm.primary`, 10 `llm.secondary`,
  and five `vision.general`, all on `primary-local` with no fallback.

The first restored subset forced thinking off and failed strict JSON/Responses
because GLM concatenated hidden reasoning into visible content. The same exact
serve passed under the qualified `reasoning_effort=low` control. This is a
client-control compatibility boundary, not a backend crash.

## Failures and caveats

- DFlash2's published license is noncommercial and no-derivatives. Obtain
  separate permission before commercial use.
- The target/draft/runtime are community artifacts, not first-party Z.ai
  distribution or stock-vLLM support.
- Video is disabled and absent from the router/client catalogs.
- The DFlash2 draft receives text-only draft inputs for image requests; the
  target still performs the full visual verification.
- WSL2 cannot use the upstream peer-IPC collectives on this host; PyNCCL is the
  locally proven fallback and may explain part of the gap to native Linux.
- Short C16 aggregate throughput is noisy because generated answer lengths are
  not fixed. K3 is an alternate, not a universal throughput win.
- The router exposes c16 scheduling, but the KV pool only proves 2.78 full
  windows.
- OpenClaw initially inherited a stale service-local router credential after
  token rotation. A mode-700 rollback bundle was created, only that environment
  entry changed, and the running-gateway path then passed. The dedicated
  `harness restart openclaw` command also exposed a PATH defect; launchd was
  used for the bounded restart and the defect remains a product ticket.
- No current Docker-image removal product surface exists for exact image IDs.
  The superseded runtime image was retained rather than using a broad prune;
  the previous GLM image is also intentionally retained as the one-week
  rollback.

## Storage cleanup

The exact unused
`inclusionAI/Ling-3.0-flash-fp8@a5d248fcca98b9d9a0c225cc22372f2fd1b3540b`
snapshot was verified unreferenced and removed through the managed cache
surface. The operation reclaimed 128,468,080,072 logical bytes. Recovery
requires redownloading that exact revision. No broad cache or Docker-image
prune and no VHDX compaction occurred.

## Evidence boundary

This evidence qualifies and promotes the exact pinned K5 profile on the named
dual-Max-Q/WSL2 host. It proves locally bounded text/tool/image/OCR behavior,
near-maximum retrieval, the stated depth/concurrency measurements, exact
routed identity, and the listed real-client paths. It does not prove video,
commercial licensing, stock-vLLM portability, native-Linux performance,
sixteen full-window requests, or universal intelligence superiority.

The previous 262K fixed-K5 GLM image/profile remains the rollback for the
one-week evaluation. Any target revision, runtime/image, GPU product, DFlash2
depth, KV dtype, context, or transport change requires requalification.
