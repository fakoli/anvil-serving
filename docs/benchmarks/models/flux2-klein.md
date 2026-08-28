# FLUX.2 Klein 4B

## Current status and review date

Production-enabled image-generation workflow with functional, capacity,
routed/client, lifecycle, and bounded-quality evidence;
`available=true`, `promoted=false`. Review date: 2026-08-28.

## Immutable identity

`black-forest-labs/FLUX.2-klein-4b-fp8` revision
`5b4408e59397a4a37ccb46afe426d8ed86379441`, combined with the Qwen3 4B
encoder and FLUX.2 VAE from `Comfy-Org/flux2-klein` revision
`5f526678002e43af5551dadb73ce2e8c91b43afe`. Exact file sizes and SHA-256
identities are recorded in the workflow bundle lock and dated evidence.

## Tested hardware and topology

One RTX 5090, 32,607 MiB, in a Windows 11 / Docker Desktop / WSL2 media lane.
Direct qualification, isolated exact-build acceptance, and the production
cross-host path with Fakoli Dark gateway, Fakoli Mid Mod resource ownership,
and real Hermes on Fakoli Mini were tested. The production router and bounded
controllers changed; the unrelated Qwen service remained in place.

## Engine, quantization, KV, context, and concurrency recipe

ComfyUI v0.33.4 at `7a131a3a`, CUDA 13.0, PyTorch 2.13.0+cu130, digest-pinned
base, curated node pins, workflow `image.flux2-klein-4b-fp8-v1` at graph digest
`991b63b8...2e4f`, c1. The server-owned profiles are `draft` 512×512,
`standard` 768×768, and `high` 1024×1024, all four steps. KV cache and
language-model context are not applicable to this diffusion workflow.

## Evidence by measurement class

`functional`, `capacity`: decodable 258,472-byte PNG in 9.859 seconds, peak
12,919 MiB from a 943 MiB worker baseline, max queue running one and pending
zero. An isolated exact-build pass added two prompt-adherent PNGs and complete
artifact controls. The production pass then added one real-Hermes warm request
per fixed profile and one fully cold `draft`; all four passed independent
bounded review. Warm gateway E2E measured 1.352/1.242/1.650 seconds for
draft/standard/high. The 2,045.626-second cold E2E includes multiple
fix-forward deployment failures and is not steady-state latency.

## Decision and promotion state

Enable the exact workflow at the three fixed c1 profiles. This is workflow
availability, not a model-family promotion or a claim of broad image quality.
Caller-supplied graphs, models, node paths, dimensions, and alternate
generators remain unavailable.

## Failures and gotchas

The first qualification attempts exposed missing native build dependencies in
the derived runtime. The production cold path later exposed controller timeout,
Buildx, read-only client state, no-router scoping, approval recovery, descendant
cleanup, and nested receipt defects; each was fixed forward with regression
coverage. One cold c1 run is not a throughput distribution or latency target.
Four visually successful images over two prompts do not establish broad text
rendering, hands, counting, seed repeatability, or monotonic quality by tier.

## Dated run history

- [2026-08-28 Hermes image-quality production enablement](../../findings/2026-08-28-hermes-image-quality-production.md)
- [2026-08-28 media gateway live validation](../../findings/2026-08-28-media-gateway-live-validation.md)
- [2026-08-28 ComfyUI media qualification](../../findings/2026-08-28-comfyui-media-qualification.md)
- [2026-08-27 feasibility screen](../../findings/2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
