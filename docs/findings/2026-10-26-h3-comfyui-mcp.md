# Cobijada H3 generation and guided upscale through MCP on RTX5090

**Date:**2026-10-26. **Evidence:**functional. **Decision:**no-promotion; private bounded workflow enabled under existing authorization, public templates unavailable.

<!-- benchmark-result-card/v1 -->
## Result card

The installed MCP completed local Cobijada H3 generation, owned-artifact handoff and guided2× finishing on one RTX5090, then released its worker.

| Setup | Recorded configuration |
| --- | --- |
| Model | Pinned Cobijada H3 hybrid/pruned quant, Comfy H3 INT8 text encoder, H3 guide LoRA and BF16 3D-conv latent upscaler; [six exact assets](2026-10-26-h3-comfyui-mcp-evidence/asset-lock.json) |
| Hardware | One RTX5090,32607MiB; Windows96GB installed, WSL72GiB configured; worker64GiB RAM/72GiB RAM+swap |
| Runtime | ComfyUI0.42.0,PyTorch2.13.0+cu130; final [wheel/image identity](2026-10-26-h3-comfyui-mcp-evidence/runtime-identity.json); exact Comfy Git commit unknown |
| Recipe | [Named-workflow contract and reconstruction boundary](../H3-COMFYUI-MCP.md); concurrency1 |
| Measurement path | Installed stdio MCP→authenticated HTTP→managed local ComfyUI; one base, one warm upscale, one cold upscale retry |
| Output |124frames at24fps:864×480→1728×960;5.166667seconds;AAC32kHz stereo; all full decodes passed |

| Operation | Native Comfy time (s) | Sampled whole-GPU peak (MiB) | Container sampled current / lifetime peak (GiB) | Minimum Windows available (GiB) |
| --- | ---: | ---: | ---: | ---: |
| base | 144.770 | 31,301 | 4.408 / 4.667 | 55.85 |
| warm-upscale | 155.690 | 30,327 | 7.288 / 8.800 | 50.31 |
| cold-upscale-recheck | 174.973 | 30,133 | 7.936 / 9.604 | 48.99 |

**Why it matters:**the podcast system can call bounded workflows and pass opaque owned artifacts without submitting arbitrary graphs or paths. Generation has only1306MiB of sampled whole-card headroom, so exclusive GPU admission matters.

**Important caveat:**this is a short synthetic functional smoke. Recorded voices, character LoRAs, correct three-host speaking attribution and production-length continuity remain unqualified. Generated clips/frames are withheld pending output-rights clearance.

[Artifact manifest](2026-10-26-h3-comfyui-mcp-evidence/artifact-manifest.json) · [Evidence index](2026-10-26-h3-comfyui-mcp-evidence/README.md) · [Publication summary](2026-10-26-h3-comfyui-mcp-evidence/publication-summary.md) · [Companion research](2026-10-26-h3-comfyui-mcp-evidence/research/brief.md)

## Outcome and decision

The first cold request persisted an approval/resume bundle. The separate operator MCP started the declared worker; replaying the same request submitted exactly one prompt. The upscale consumed the caller's owned MP4 artifact, then a final cold upscale exercised the corrected package from a stopped worker. Completed-job replay returned created=false and the same job. Artifact inspection/export passed SHA and byte-length checks; wrong bearer tokens returned401. A pending cold job canceled before worker preparation. Running cancellation has focused test coverage but was not exercised by interrupting a live GPU run.

Actual MCP teardown stopped and removed only the owned H3 worker. Independent readback showed108MiB whole-GPU use afterward. The gateway, dedicated lifecycle controller and safe Windows guard remain available; incumbent models stayed stopped and the original resource controller was preserved. See [restoration](2026-10-26-h3-comfyui-mcp-evidence/restoration.json).

## Exact configuration

The [asset lock](2026-10-26-h3-comfyui-mcp-evidence/asset-lock.json) binds52,838,923,136bytes across six assets. The final worker image is `sha256:58d09e9aefead16989803ef80fda4e73ab7e8052e0b41984ed55612dc0d05f3c`; its retained Comfy base lacks Git metadata. Preserve that image or independently qualify a reconstruction. Only the pinned upsampler node was enabled. Read-only model/node mounts,offline flags,PyTorch attention,no pinned memory,cache-none,reserve-vram1 and a writable temporary directory define this recipe. Front bridging makes host ports reachable; offline flags do not prove network isolation.

The admission envelope reserves31583MiB plus1024MiB global headroom on32607MiB. Legacy-controller/Qwen previews both denied overlapping H3 reservations. Windows RAM/commit guards fail closed on stale/unsafe signals and supervise only the worker child; worker lifetime is45minutes. Policies are operator-owned, not caller parameters.

## Method and timing limits

The [workload](2026-10-26-h3-comfyui-mcp-evidence/workload.md) reuses the prior fictional two-shot cafe prompt and seed42. Whole-GPU and Windows memory were sampled about1Hz; cgroup memory about2.2seconds, using queued-to-terminal windows with a1second margin. Current peaks are sampled; kernel lifetime peaks are cumulative and can exceed sampled current. No captured phase had cgroup OOM events or swap use. Raw phase rows and [derived measurements](2026-10-26-h3-comfyui-mcp-evidence/measurements.json) preserve the calculation.

The initial observer mislabeled144.9seconds as queue and0.038seconds as generation. The native log reports144.77seconds; the tiny generation field is invalid as model time. The shared adapter now checks the live queue. The final retry exposed RUNNING and recorded175.015seconds observed generation,0.192queue and719.304end-to-end, including544.097seconds before queuing. Native execution messages measured174.973seconds. Do not count approval/operator delay as model compute, or compare the different cache/package states as a speedup benchmark.

## Video and audio checks

All three exports passed complete video/audio decoding and actual124frame counts. SaveVideo passes the decoded soundtrack through the upscale graph but re-encodes AAC. A separate FFmpeg stream-copy finish replaced that audio track with the original source AAC: audio SHA256 `e3ebbdcc8d8e3dc4dde08ec1768aebbcde8072005b9114972ad3e0562d4e02a8`; refined H264 SHA256 `b354e1e35781b51eb5fce40fa0c6e9b2892cfcdfa2997c3a9b4a0d17441ef569` was unchanged. [Verification](2026-10-26-h3-comfyui-mcp-evidence/video-verification.json). Bit identity does not prove lip sync or correct speaker attribution. Static2.5second samples were inspected; no new full listening/perceptual score was collected.

## Failures and remaining gates

The publisher hit WinError5 during heartbeat replacement after the first outputs completed. Its supervisor correctly stopped the idle worker without losing files. A bounded retry fixed transient sharing denial; native held-handle probes reproduced the old failure and passed against source and installed wheel, followed by the final cold upscale. Permanent errors still become stale-signal refusal. The competing reader was not identified. Guard login registration is installed; a full logoff/reboot test remains outstanding.

The [friction ledger](2026-10-26-h3-comfyui-mcp-evidence/friction-log.md) also records the broken client, temporary-directory, healthcheck, network, MCP context and timing corrections. Scoped tests, independent code review and wheel checks support those fixes; the complete native Windows suite has an existing Linux-only collection failure. This does not establish blanket platform compatibility.

## Next experiments

The [source-heavy dossier](2026-10-26-h3-comfyui-mcp-evidence/research/brief.md) ranks native-Windows LeapTalk, source-shot MuseTalk, an authored Blender/Rhubarb/Kdenlive studio, FLUX reference keyframes and InfiniteTalk V2V. Exact-hardware evidence, paper-task boundaries, contrary reports, license/component caveats and reusable recipes stay separate from local qualification. No research model was installed.

For H3, first test native reference conditioning plus aligned master audio on a short held-out excerpt. Keep the recorded master authoritative; audio preservation alone cannot align mouths. Try NVFP4 text encoding or matched Turbo adapters only as controlled quality/memory experiments. Defer LoRA training until references fail a held-out identity test. A published monolithic Relay consumed110.32GiB RAM plus62.31GiB swap, outside this worker's envelope; staged shot assembly is the more plausible next design.
