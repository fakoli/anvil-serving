# ADR-0041 — Initial media workflows and bounded operating policy

- **Status:** Accepted for implementation; live availability remains qualification-gated
- **Date:** 2026-08-27
- **Amended:** 2026-08-28 — explicit image quality profiles, latency, and Hermes delivery
- **Relates to:** ADR-0017; ADR-0036; ADR-0040
- **Sources observed:**
  [ComfyUI FLUX.2 Klein tutorial](https://docs.comfy.org/tutorials/flux/flux-2-klein),
  [BFL FLUX.2 Klein 4B FP8 artifact](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8),
  [ComfyUI Wan2.2 tutorial](https://docs.comfy.org/tutorials/video/wan/wan2_2)

## Context

The gateway needs concrete first-release workflow IDs and fail-closed limits so
the implementation does not invent defaults during a request. Model fitness,
license acceptance, host capacity, and perceptual quality are deployment gates,
not facts implied by selecting a candidate.

## Decision

The first release implements two immutable candidate descriptors. A descriptor
is discoverable but reports `available = false` until its exact graph, required
models, runtime compatibility, functional artifact, capacity envelope, and
independent quality disposition are all proven on the selected media worker.

| Policy | Image candidate | Video candidate |
| --- | --- | --- |
| Stable ID | `image.flux2-klein-4b-fp8-v1` | `video.wan2.2-ti2v-5b-v1` |
| Kind | text-to-image | text-to-video |
| Output | `image/png` | `video/mp4` |
| Maximum dimensions | 1024 × 1024 | 832 × 480 |
| Maximum frames / duration | one image | 81 frames / 5.1 seconds at 16 fps |
| Accepted media input | none | none |
| Maximum request JSON | 65,536 bytes | 65,536 bytes |
| Maximum retained artifact | 33,554,432 bytes | 268,435,456 bytes |
| Execution timeout | 600 seconds | 3,600 seconds |
| Retention | 24 hours | 24 hours |
| Per-principal queued jobs | 2 | 2 |
| Total queued jobs | 4 | 2 |
| Backend concurrency | 1 | 1 |

The image candidate follows ComfyUI's native distilled FLUX.2 Klein 4B
workflow shape. Its pinned diffusion model, text encoder, and VAE total
12,451,817,860 bytes. The previously considered FLUX.2 Dev FP8-mixed bundle
was removed before download: its three required checkpoints total
71,376,710,595 bytes, exceeding the target host's combined 32 GB VRAM and
approximately 31 GB RAM even before runtime workspace and operating-system
reserves. That is a feasibility rejection of the exact recipe, not a quality
judgment about FLUX.2 Dev.

The video baseline deliberately chooses the native Wan2.2 TI2V 5B
workflow before the larger two-expert 14B variants: the official ComfyUI guide
states that the 5B workflow supports text and image conditioning and fits with
native offload on much smaller VRAM. This is a feasibility prior, not local
qualification. Wan2.2 A14B remains a named research candidate but is not a
first-release remotely callable workflow until separately pinned and measured.

Only `prompt`, `seed`, `width`, `height`, and the workflow-specific bounded
sampling or frame fields may be bound. Raw graphs, node IDs, model filenames,
paths, URLs, and installation choices are never caller inputs. Seeds are
unsigned 64-bit integers; prompts are at most 4,096 UTF-8 characters.

The image descriptor may expose three explicit quality profiles:

| Profile | Exact locked parameters | Meaning |
| --- | --- | --- |
| `draft` | 512 × 512, four steps | smallest and fastest declared image setting |
| `standard` | 768 × 768, four steps | default balanced setting |
| `high` | 1024 × 1024, four steps | largest declared image setting |

All profiles resolve inside the same immutable workflow. They do not select a
different model, host, backend, or provider, and the caller cannot override a
profile-owned field. Profile labels describe settings rather than guaranteeing
perceptual quality. Availability still requires independent evidence for the
settings made callable by an operator deployment.

## Lifecycle and exposure policy

- Cold ComfyUI never starts from an ordinary media call. The durable job enters
  `awaiting_approval` and exposes the exact managed-operation preview.
- A reviewed private operator policy may pre-authorize the existing confirmed
  lifecycle transaction, but public defaults do not.
- The ComfyUI UI is loopback-only and disabled at the public edge by default.
  Enabling it is an operator-only support choice and does not make it an agent
  API.
- The Hermes baseline is the bundled Node 20+ stdio compatibility bridge to
  modern stateless MCP. Direct Streamable HTTP is the preferred transport once
  the installed Hermes MCP client proves the `2026-07-28` contract.
- Full media is retained behind an authenticated resource link. An image no
  larger than six binary MiB may additionally be returned as native MCP image
  content; its base64 envelope stays below the ten-MiB controller/SDK framing
  bound. Larger images and all video remain resource-only.
- The selected quality profile is durable job metadata. Job status derives
  phase and end-to-end latency from ordered durable events. MCP artifact
  inspection may additionally return a bounded native image content block so
  Hermes can present the generated image without exposing a credential.
- A profile-aware caller uses the published `profiledParameterSchema`; the
  complete legacy `schema` remains compatible. Qualification must decode the
  artifact and prove its dimensions match the exact selected profile.
- Retention expiry removes the Anvil-owned copy. Source backend files are not
  an artifact API and are governed by the media-worker's private policy.

## Qualification blockers

The following are explicit blockers, not guessed settings:

1. exact upstream repository revisions and SHA-256 identities for every model
   file and workflow graph;
2. license review for the exact image weights and intended use;
3. successful compatibility checks against bounded ComfyUI feature, node, and
   model inventories;
4. measured peak GPU memory, host memory, latency, queue behavior, and output
   size on the target 32 GB discrete-GPU class;
5. decodable PNG/MP4 artifacts with complete provenance and successful managed
   rollback; and
6. an independent perceptual-quality disposition.

The perceptual-quality owner is a human or separately administered evaluation
harness that did not generate the artifact and does not use the generation
model to validate itself. The adapter owns transport and format checks only.
No workflow becomes available or promoted merely because it is ready, runs, or
produces decodable bytes.

## Consequences

The limits are intentionally conservative and may be revised only through a
new reviewed workflow version or policy decision backed by measured evidence.
Unknown values keep a workflow unavailable. There is no automatic fallback to
the other candidate, a larger model, another host, or a cloud provider.
