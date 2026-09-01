# Wan2.2 TI2V 5B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Unavailable text-to-video candidate with functional,
      capacity, routed, and client evidence but a blocking perceptual failure.
    - **Selected or best-qualified configuration:** Pinned ComfyUI v0.33.4
      workflow `video.wan2.2-ti2v-5b-v1`, concurrency one, 512×288, 17 frames,
      eight steps, and 16 fps.
    - **Measured hardware:** One 32,607 MiB RTX 5090 in an isolated Windows 11,
      Docker Desktop, and WSL2 media lane.
    - **Evidence:** A direct 111,055-byte H.264 MP4 completed in 9.092 seconds;
      a real-Hermes request produced a 117,738-byte MP4 with the same shape.
      Independent contact-sheet review found severe spatial and prompt-
      adherence artifacts.
    - **Decision:** `available=false`, `no-promotion`; routed transport works,
      but the exact workflow failed the bounded quality gate.
    - **Important limitation:** Generated MP4 binaries were not retained, and
      temporal smoothness, motion, camera behavior, longer clips, multiple
      prompts/seeds, and concurrency above one were not tested.
    - **Review dates:** Retained evidence cutoff: 2026-08-28. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-08-27 — Feasibility screen

The workflow passed the paper and configuration feasibility screen for the
isolated RTX 5090 media lane. That result only admitted the candidate to local
qualification; it did not establish runtime or output quality.

#### 2026-08-28 — Direct workflow qualification

After correcting the immutable graph for VideoHelperSuite's required
`save_output` input, the pinned workflow produced a decodable 111,055-byte
H.264 MP4 in 9.092 seconds. Functional and c1 capacity gates passed. Independent
contact-sheet review then failed prompt adherence and spatial quality because
severe artifacts dominated the frames.

#### 2026-08-28 — Gateway and Hermes validation

An exact-build cross-host candidate passed gateway transport and a real Hermes
request, producing a second decodable 117,738-byte H.264 MP4. This proved the
routed/client path, not model quality. The existing production router and
unrelated inference services were unchanged.

## Immutable identity

### Model files

- Repository: `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`.
- Revision: `c4f60d30c55a624e35427060fdd217579a6c1d77`.
- Components: diffusion model, UMT5 XXL FP8 scaled encoder, and VAE.

Exact file sizes and SHA-256 identities are recorded in the public
[workflow bundle lock](https://github.com/fakoli/anvil-serving/blob/main/configs/media/workflows/bundle.lock.json) and
the dated evidence.

### Runtime and workflow

- Base container:
  `obeliks/comfyui@sha256:4a40acf4790733dfeb38a92dc2b976c812a484264ff08362e7f8c0d5a16df3e9`.
- ComfyUI revision: `7a131a3afadc8200120f67f9236311a2c48b7445`.
- VideoHelperSuite revision: `4ee72c065db22c9d96c2427954dc69e7b908444b`.
- Workflow: `video.wan2.2-ti2v-5b-v1`.
- Graph digest:
  `bd12b2de2a33bbedc91d7ad6120714f3c3adbd174694ac74d6f0213ecef9572e`.

## Tested hardware and topology

### Direct lane

- One RTX 5090 with 32,607 MiB.
- Windows 11, Docker Desktop, and WSL2.
- Isolated media workload at concurrency one.

### Routed client lane

- Exact-build candidate with Fakoli Dark gateway and a real Hermes client.
- Production router and unrelated inference services remained unchanged.

Other accelerators, multi-GPU execution, and co-resident media generation were
**not tested**.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified workflow shape

- ComfyUI v0.33.4, CUDA 13.0, and PyTorch 2.13.0+cu130.
- Pinned VideoHelperSuite custom node.
- Concurrency: one.
- Resolution: 512×288.
- Frames: 17.
- Sampling steps: eight.
- Frame rate: 16 fps.
- KV cache and language-model context: **Not applicable** to this diffusion
  workflow.

The [immutable workflow graph](https://github.com/fakoli/anvil-serving/blob/main/configs/media/workflows/video.wan2.2-ti2v-5b-v1.json)
and bundle lock provide the public reconstruction inputs.

## Evidence by measurement class

### Direct functional and capacity evidence

- Output: decodable 111,055-byte H.264 MP4.
- Runtime: 9.092 seconds.
- Shape: 512×288, 17 frames, 1.0625 seconds.
- GPU memory: 18,263 MiB peak from a 943 MiB worker baseline.
- Queue: maximum running one, pending zero.

### Routed and client evidence

- Real-Hermes output: decodable 117,738-byte H.264 MP4.
- Shape: same resolution, frame count, and duration as the direct run.

### Bounded quality evidence

Independent contact-sheet review failed prompt adherence and spatial quality
because severe artifacts dominated the frames. The contact sheet could not
assess temporal smoothness or camera motion.

### Evidence retention

Structured metadata and contact-sheet evidence are retained. The generated MP4
binaries themselves are **not retained**.

## Decision and promotion state

### Retained unavailable candidate

- `available=false`.
- `no-promotion`.
- Gateway and Hermes transport work for the exact workflow.

### Reconsideration gate

A new immutable workflow version plus multi-prompt and temporal review is
required before another enablement decision. This record does not authorize a
route or deployment change.

## Failures and gotchas

### Corrected workflow defect

The pinned VideoHelperSuite revision requires `save_output`. The immutable
graph and digest were corrected before the passing run; the earlier graph is
not the qualified recipe.

### Quality and scope limitations

- Severe spatial and prompt-adherence artifacts are independently blocking.
- Temporal smoothness, motion, and camera behavior: **Not tested**.
- Longer clips, multiple prompts/seeds, and concurrency above one:
  **Not tested**.
- Generated MP4 binaries: **Not retained**.
- One short c1 run does not qualify general throughput.

## Dated run history

- [2026-08-28 media gateway live validation](../../findings/2026-08-28-media-gateway-live-validation.md)
- [2026-08-28 ComfyUI media qualification](../../findings/2026-08-28-comfyui-media-qualification.md)
- [2026-08-27 feasibility screen](../../findings/2026-08-27-media-gateway-comfyui-evidence/feasibility-result.md)
