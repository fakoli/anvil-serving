# Wan2.2 TI2V 5B

## Current status and review date

Functionally, capacity, and routed/client qualified text-to-video candidate
with a bounded perceptual failure;
`available=false`, `no-promotion`. Review date: 2026-08-28.

## Immutable identity

The diffusion model, UMT5 XXL FP8 scaled encoder, and VAE come from
`Comfy-Org/Wan_2.2_ComfyUI_Repackaged` revision
`c4f60d30c55a624e35427060fdd217579a6c1d77`. Exact file sizes and SHA-256
identities are recorded in the workflow bundle lock and dated evidence.

## Tested hardware and topology

One RTX 5090, 32,607 MiB, in an isolated Windows 11 / Docker Desktop / WSL2
media lane. Direct qualification and an exact-build cross-host candidate with
Fakoli Dark gateway and real Hermes client were tested. Production router and
unrelated inference services were unchanged.

## Engine, quantization, KV, context, and concurrency recipe

ComfyUI v0.33.4 at `7a131a3a`, CUDA 13.0, PyTorch 2.13.0+cu130,
VideoHelperSuite at `4ee72c06`, workflow `video.wan2.2-ti2v-5b-v1` at graph
digest `bd12b2de...9572`, c1. The clean-baseline request was 512×288, 17
frames, eight steps, and 16 fps. KV cache and language-model context are not
applicable to this diffusion workflow.

## Evidence by measurement class

`functional`, `capacity`: decodable 111,055-byte H.264 MP4 in 9.092 seconds,
17 frames and 1.0625 seconds duration, peak 18,263 MiB from a 943 MiB worker
baseline, max queue running one and pending zero. A later real-Hermes request
produced a second decodable 117,738-byte H.264 MP4 with the same dimensions,
frame count, and duration. Independent contact-sheet review failed prompt
adherence and spatial quality because severe artifacts dominated the frames.

## Decision and promotion state

Retain as an unavailable, `no-promotion` candidate. Routed/Hermes transport
works, but this exact workflow version failed the bounded quality gate. A new
immutable workflow version and multi-prompt temporal review are required before
another enablement decision.

## Failures and gotchas

The pinned VideoHelperSuite revision requires `save_output`; the immutable
graph and digest were corrected before the passing run. One short c1 run does
not qualify longer clips, concurrency, temporal quality, or throughput.
The later contact sheet cannot assess temporal smoothness or camera motion, but
its visible spatial and prompt-adherence failure is independently blocking.

## Dated run history

- [2026-08-28 media gateway live validation](../../findings/2026-08-28-media-gateway-live-validation.md)
- [2026-08-28 ComfyUI media qualification](../../findings/2026-08-28-comfyui-media-qualification.md)
- [2026-08-27 feasibility screen](../../findings/2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
