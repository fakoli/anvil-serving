# Evidence manifest: ComfyUI image and video qualification

The [dated finding](../2026-08-28-comfyui-media-qualification.md) is
authoritative. This directory contains bounded, sanitized metadata only. It
contains no generated media bytes, prompts, private endpoints, GPU UUIDs, host
paths, or credentials.

| Artifact | Role | Supported claims | Boundary |
|---|---|---|---|
| [`image-qualification.json`](image-qualification.json) | FLUX.2 Klein functional and capacity qualification | exact identities, queued job lifecycle, decodable 512×512 PNG, latency, peak VRAM, and no promotion | one cold c1 qualification; no perceptual quality disposition |
| [`video-qualification.json`](video-qualification.json) | Wan2.2 functional and capacity qualification | exact identities, queued job lifecycle, 17-frame decodable H.264 MP4, latency, peak VRAM, and no promotion | one clean-baseline cold c1 qualification; no perceptual quality disposition |
| [`rollback.json`](rollback.json) | managed lifecycle and restoration | clean stop/start plus final absent worker, released reservation, and restored GPU baseline | proves local rollback only; no routed or remote-client acceptance |
| [`publication-summary.md`](publication-summary.md) | derivative publishing copy | compact facts and claim ledger reconciled to the artifacts above | not an independent evidence source |

The separate
[2026-08-27 feasibility packet](../2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
records the pre-download capacity screening. Exact model and runtime pins are
also maintained in
[`bundle.lock.json`](../../../configs/media/workflows/bundle.lock.json).
