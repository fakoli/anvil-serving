# FLUX.2 Klein 4B

## Current status and review date

Functionally, capacity, routed/client, and bounded-quality qualified
image-generation candidate;
`available=false`, `no-promotion`. Review date: 2026-08-28.

## Immutable identity

`black-forest-labs/FLUX.2-klein-4b-fp8` revision
`5b4408e59397a4a37ccb46afe426d8ed86379441`, combined with the Qwen3 4B
encoder and FLUX.2 VAE from `Comfy-Org/flux2-klein` revision
`5f526678002e43af5551dadb73ce2e8c91b43afe`. Exact file sizes and SHA-256
identities are recorded in the workflow bundle lock and dated evidence.

## Tested hardware and topology

One RTX 5090, 32,607 MiB, in an isolated Windows 11 / Docker Desktop / WSL2
media lane. Direct qualification and an exact-build cross-host candidate with
Fakoli Dark gateway and real Hermes client were tested. Production router and
unrelated inference services were unchanged.

## Engine, quantization, KV, context, and concurrency recipe

ComfyUI v0.33.4 at `7a131a3a`, CUDA 13.0, PyTorch 2.13.0+cu130, digest-pinned
base, curated node pins, workflow `image.flux2-klein-4b-fp8-v1` at graph digest
`991b63b8...2e4f`, c1. The measured request was 512×512 and four steps. KV
cache and language-model context are not applicable to this diffusion workflow.

## Evidence by measurement class

`functional`, `capacity`: decodable 258,472-byte PNG in 9.859 seconds, peak
12,919 MiB from a 943 MiB worker baseline, max queue running one and pending
zero. A later exact-build live pass produced two additional PNGs through the
cold approval and real-Hermes paths. Both passed independent prompt-adherence
review; artifact delivery, ranges, missing-object behavior, signatures, sizes,
and hashes passed.

## Decision and promotion state

Retain as an unavailable candidate. The bounded routed/Hermes and two-sample
quality gates passed, but a broader image-quality corpus, production cutover,
and separate live-enable gate remain open.

## Failures and gotchas

The first attempts exposed missing native build dependencies in the derived
runtime. The durable image now contains the compiler and C library headers.
One cold c1 run is not a throughput distribution or quality ranking.
Two visually successful routed samples do not establish broad text rendering,
hands, counting, seed-repeatability, or production deployment quality.

## Dated run history

- [2026-08-28 media gateway live validation](../../findings/2026-08-28-media-gateway-live-validation.md)
- [2026-08-28 ComfyUI media qualification](../../findings/2026-08-28-comfyui-media-qualification.md)
- [2026-08-27 feasibility screen](../../findings/2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
