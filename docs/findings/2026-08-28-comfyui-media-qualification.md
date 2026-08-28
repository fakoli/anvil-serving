# ComfyUI FLUX.2 Klein and Wan2.2 qualification on RTX 5090

**Date:** 2026-08-28

**Decision:** retain both as unavailable candidates; `no-promotion`

**Evidence:** `functional`, `capacity`; perceptual `quality` remains unreviewed

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120

**Topology:** isolated Windows 11 / Docker Desktop / WSL2 media lane; the
router and unrelated inference services were not changed

<!-- benchmark-result-card/v1 -->
## Result card

> The exact managed FLUX.2 Klein and Wan2.2 candidate workflows each produced
> a decodable artifact from a clean 943 MiB ComfyUI worker baseline, then the
> worker was removed and GPU use returned to 448 MiB.

| Setup | Qualified value |
|---|---|
| Models | FLUX.2 Klein 4B FP8 plus Qwen3 4B encoder and FLUX.2 VAE; Wan2.2 TI2V 5B FP16 plus UMT5 FP8 encoder and Wan2.2 VAE; exact revisions and SHA-256 identities in the evidence |
| Hardware | one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120; isolated Windows 11 / Docker Desktop / WSL2 lane |
| Runtime | ComfyUI v0.33.4 at `7a131a3a`; digest-pinned base; CUDA 13.0; PyTorch 2.13.0+cu130; exact curated custom-node revisions |
| Managed recipes | `examples/fakoli-dark/Dockerfile.comfyui`, `examples/fakoli-dark/serves.comfyui.toml`, and `configs/media/workflows/registry.json` |
| Measurement path | direct loopback managed ComfyUI; qualification-only durable jobs; cold model load; c1; no router or agent client |
| Contracts | image 512×512 / four steps; video 512×288 / 17 frames / eight steps / 16 fps |
| Evidence | `functional`, `capacity`; exact model/runtime compatibility and format decoding pass |
| Decision | candidates retained unavailable, `no-promotion`; independent perceptual review remains `human_required` |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| FLUX.2 Klein latency | 9.859 s | one cold 512×512 PNG, four steps, c1 |
| FLUX.2 Klein peak GPU memory | 12,919 MiB | 943 MiB worker baseline; nine samples |
| Wan2.2 latency | 9.092 s | one clean-baseline 512×288, 17-frame H.264 MP4, eight steps, c1 |
| Wan2.2 peak GPU memory | 18,263 MiB | 943 MiB worker baseline; ten samples |
| Queue behavior | max running 1, pending 0 | both jobs returned queued immediately and completed asynchronously |
| Final restoration | worker absent; 448 MiB GPU used | reservation committed 0 MiB; 28,511 MiB free |

**Why it matters:** This closes the exact model-identity, runtime-compatibility,
functional-artifact, capacity, and rollback gates for the first bounded image
and video candidates without turning ComfyUI graphs or filesystem paths into a
caller API.

**Important caveat:** These are single cold c1 runs. The transport decoded the
files, but no independent reviewer assessed perceptual quality; no claim about
visual quality, determinism, concurrency above one, routed behavior, or normal
remote availability is supported.

[Evidence manifest](2026-08-28-comfyui-media-qualification-evidence/README.md)
· [Publication summary](2026-08-28-comfyui-media-qualification-evidence/publication-summary.md)

## Immutable identities

The image bundle contains 12,451,817,860 bytes across:

- `black-forest-labs/FLUX.2-klein-4b-fp8@5b4408e5`, SHA-256
  `97ed34fe...ccb6`;
- `Comfy-Org/flux2-klein@5f526678` Qwen3 4B encoder, SHA-256
  `6c671498...fc5a`; and
- the matching FLUX.2 VAE, SHA-256 `868fe7b3...e8f3`.

The video bundle contains 18,144,966,705 bytes across the three
`Comfy-Org/Wan_2.2_ComfyUI_Repackaged@c4f60d30` files:

- Wan2.2 TI2V 5B FP16, SHA-256 `456f9013...a1e`;
- UMT5 XXL FP8 scaled, SHA-256 `c3355d30...4f68`; and
- Wan2.2 VAE, SHA-256 `e40321bd...d156`.

The image graph digest is `991b63b8...2e4f`; the corrected video graph digest
is `bd12b2de...9572`. Full values and byte counts are retained in the raw
metadata and public bundle lock.

## Method and measurements

Exact assets were staged into named Docker volumes through `media bundle
stage`, with size and SHA-256 verification before atomic placement. The worker
was built and started through `serves up`; compatibility queried bounded
feature, per-node, and model inventories. `media qualify run` submitted an
immutable rendered graph, recorded the durable job state sequence, sampled the
ComfyUI queue and GPU memory, fetched the bounded artifact into Anvil-owned
storage, and independently decoded PNG structure or `ffprobe` MP4 metadata.

Prompt text is intentionally omitted; only a SHA-256 digest is public. The
generated media bytes are also omitted because format and capacity evidence do
not require publishing model output, and perceptual review remains open.

The image produced one 258,472-byte PNG with the requested 512×512 dimensions.
The final clean-baseline video produced one 111,055-byte H.264 MP4 with 17
frames at 16 fps, 512×288, and 1.0625 seconds duration. The reciprocal
single-run rates in raw evidence are not throughput distributions and are not
used as headline throughput claims.

## Failure record

The qualification fixed durable product/runtime defects rather than applying
one-off bypasses:

1. BusyBox staging checksum flags were made portable and exact complete
   partials became resumable.
2. Root-owned image builds gained an explicit Git safe-directory check against
   the ComfyUI release revision; bounded lifecycle build output now survives
   Windows decoding and redacts credentials.
3. Compatibility moved from the oversized global `/object_info` document to
   bounded per-required-node queries and real ComfyUI feature flags.
4. The derived image now explicitly contains `gcc` and `libc6-dev`, required
   by Triton's runtime driver compilation under `--no-install-recommends`.
5. The pinned VideoHelperSuite graph gained its required immutable
   `save_output=true` field and a new canonical digest.

Each failure occurred before a successful artifact claim, is retained in a
public ticket, and was followed by the same managed qualification path.

## Decision boundary

Keep both descriptors discoverable but `available=false`. The successful
qualification-only compatibility override is not a promotion mechanism. A
separately administered perceptual reviewer must assess the retained or newly
generated artifacts before changing the quality disposition. Routed MCP/A2A
acceptance, Hermes skill acceptance, normal remote enablement, and any final
live deployment remain separate gates.

The final managed rollback removed the worker, released the entire 28,511 MiB
reservation, and returned host GPU use to 448 MiB. No router configuration,
route, deployment assignment, or unrelated model service changed.

## Raw evidence

- [Image qualification](2026-08-28-comfyui-media-qualification-evidence/image-qualification.json)
- [Video qualification](2026-08-28-comfyui-media-qualification-evidence/video-qualification.json)
- [Managed rollback](2026-08-28-comfyui-media-qualification-evidence/rollback.json)
- [Pre-download feasibility](2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
