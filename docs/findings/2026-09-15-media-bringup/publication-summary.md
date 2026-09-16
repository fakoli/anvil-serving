# Publication summary: managed Anvil Media bringup

<!-- benchmark-publication-summary/v1 -->

This derivative summary does not broaden the functional smoke into a quality,
latency, capacity, or promotion claim.

## Canonical facts

- **Model identity:** FLUX.2 Klein image workflow and Wan2.2 TI2V video
  workflow; exact video repository revision `c4f60d30c55a624e35427060fdd217579a6c1d77`.
- **Runtime identity:** ComfyUI v0.33.4, CUDA 13.0, PyTorch 2.13.0+cu130;
  derived-image identity not independently verified.
- **Measured hardware:** one RTX 5090, 32,607 MiB, c1.
- **Recipe:** [retained request inputs](workload.json), executed with the
  [managed media CLI](../../cli/media.md); graph identity and parameters are
  recorded in the [finding](../2026-09-15-media-bringup.md#configuration-and-evidence).
- **Measurement path:** direct managed ComfyUI worker, one sample per configuration.
  First two videos followed image caching; standard v2 followed Wan caching.
  Image cold/warm state was not recorded.
- **Headline result:** one 1024x1024 PNG plus v1 and two v2 decodable H.264
  MP4 diagnostics completed; v2 quality remained partial.
- **Important caveat:** image refresh delay is not generation latency; Wan2.2
  remains quality-blocked and unavailable.
- **Decision:** `no-promotion`; `promoted=false`; worker left running.
- **Canonical evidence:** [dated finding](../2026-09-15-media-bringup.md).
- **Artifact set:** [manifest](artifact-manifest.json) and [evidence index](README.md).

## X / short post

```text
Local RTX 5090 media smoke: FLUX.2 produced a 1024x1024 image. A repaired Wan2.2 graph restored recognizable video, but quality remains unverified. No promotion. https://fakoli.github.io/anvil-serving/findings/2026-09-15-media-bringup/
```

## Reddit

```text
Local RTX 5090 media smoke: FLUX.2 image passed; corrected Wan2.2 video remains experimental
```

```markdown
This local test used one RTX 5090, ComfyUI v0.33.4, and one job per configuration at concurrency one.

FLUX.2 Klein produced a 1024x1024 PNG at four steps. Wan2.2 v1 produced a decodable but visually failed clip. A corrected v2 graph produced recognizable content at 512x288/33 frames and 832x480/49 frames, but both sampled-frame reviews remained partial.

These are functional checks, not a speed comparison. Temporal quality was not assessed, and video remains unavailable with no promotion.

Inputs, failures, and sanitized evidence: https://fakoli.github.io/anvil-serving/findings/2026-09-15-media-bringup/
```

## Screenshot alt text

An Anvil Media bringup on one RTX 5090 completed a fixed high-resolution image
job and three decodable Wan2.2 video diagnostics. The worker remained healthy and
running. The record makes no performance claim and keeps Wan2.2 unavailable
because v1 quality failed and both corrected v2 reviews remained partial.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Fixed image job completed | high 1024x1024, four steps, seed 915 | [finding](../2026-09-15-media-bringup.md) · [image record](image-job.json) |
| Fixed video job decoded | 512x288, 33 frames, 16 fps, c1 | [finding](../2026-09-15-media-bringup.md) · [video record](video-qualification.json) |
| Corrected v2 diagnostics decoded | 512x288/33 frames/20 steps and 832x480/49 frames/30 steps | [small](video-v2-qualification.json) and [standard](video-v2-standard-qualification.json) |
| Worker left running | managed status, HTTP 200 | [finding](../2026-09-15-media-bringup.md) · [restoration](restoration.json) |
| No promotion or video quality approval | prior `quality_failed`; current quality human-required | [finding](../2026-09-15-media-bringup.md) · [summary](summary.json) |
