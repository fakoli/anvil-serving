# 2026-09-15 Anvil Media managed bringup

**Decision:** `no-promotion`; no workflow availability, route, alias, or serve promotion changed. Post-run health passed; actual operational state remains in the private operator repository.

<!-- benchmark-result-card/v1 -->
## Result card

**Setup and measurement:** one RTX 5090 (32,607 MiB), ComfyUI v0.33.4,
CUDA 13.0 / PyTorch 2.13.0+cu130, one job at a time. The direct managed-worker
path used `media workflow run` plus status refresh for the image and
`media qualify run` for the videos. The [reproduction inputs](2026-09-15-media-bringup/workload.json)
pin prompts and parameters; the [CLI reference](../cli/media.md) describes both commands.
The first two videos followed image-model caching; the standard v2 run followed
Wan caching. Image cold/warm state was not recorded. Each row is one sample.

| Lane | Retained result | Quality / decision |
|---|---|---|
| FLUX.2 high | One 1024x1024, four-step PNG (seed 915), 1,475,793 bytes | One independent review passed with a minor brushed-metal mismatch; existing availability unchanged |
| Wan v1 | 512x288, 33-frame H.264, 124,646 bytes | Decodable but independent review failed frames 0/16/32; `quality_failed`, unavailable |
| Wan v2 small | corrected `video.wan2.2-ti2v-5b-v2@v2`, 512x288, 33-frame H.264, 68,494 bytes | recognizable content recovered, but partial review; `quality_unverified`, unavailable |
| Wan v2 standard | 832x480, 49-frame H.264, 153,208 bytes | partial review: no major collapse, but cropped handle, misplaced steam, synthetic artifacts; preview did not pass full quality |

The jobs differ in graph, prompt, resolution, frame count, and steps. Their n=1 recorded latencies are functional diagnostics only, never a performance comparison or sustained-rate estimate. The image 38.335-second status delay is refresh delay, not image generation latency.

**Why it matters:** the corrected graph restored recognizable video content,
but the retained spatial reviews do not establish temporal quality or justify
enabling video. [Manifest](2026-09-15-media-bringup/artifact-manifest.json) ·
[Evidence index](2026-09-15-media-bringup/README.md) ·
[Publication summary](2026-09-15-media-bringup/publication-summary.md).

## Configuration and evidence

The image used the fixed `high` profile, 1024x1024, four steps, seed 915. Wan v1 used `video.wan2.2-ti2v-5b-v1`. Wan v2 used graph SHA-256 `b93170eeb578ce5a8dc86d2dfbd944e037abf2e8b9a223059f8e9c6aa8291c14`; small used 512x288/33 frames/20 steps and standard used 832x480/49 frames/30 steps with the explicit curved-handle prompt. Request inputs and sanitized results are retained in the [evidence bundle](2026-09-15-media-bringup/README.md). The [redaction record](2026-09-15-media-bringup/redaction-provenance.json) distinguishes public derivatives from private native receipts.

The v2 repair follows the [official Wan2.2 tutorial](https://docs.comfy.org/tutorials/video/wan/wan2_2) and [5B example graph](https://comfyanonymous.github.io/ComfyUI_examples/wan22/text_to_video_wan22_5B.json): it uses `Wan22ImageToVideoLatent` with the VAE, separate negative conditioning, and `ModelSamplingSD3` shift 8. Runtime reports identify ComfyUI v0.33.4, CUDA 13.0, and PyTorch 2.13.0+cu130; the derived image identity was not independently verified. It improved sampled spatial recognizability but does not authorize availability. Temporal behavior was not assessed.

## Operational state and boundaries

The post-run managed health check passed. Actual service and container identities,
ports, GPU reservations, placement, co-resident assignments, and raw status
receipts are retained only in the private operator repository. This public
finding describes the bounded test outcome, not a live deployment inventory.

The image replay returned `created=false` for the same completed job; an unrelated principal received `artifact_not_found`. The topology-dispatch defect remains tracked in the [ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-15-media-worker-topology-dispatch.md). No private host or operator identity is published.

Artifact manifest: [artifact-manifest.json](2026-09-15-media-bringup/artifact-manifest.json).
