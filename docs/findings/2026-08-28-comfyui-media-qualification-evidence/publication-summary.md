# Publication summary: local ComfyUI image and video qualification

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-08-28-comfyui-media-qualification.md) and linked raw
metadata are authoritative.

## Canonical facts

- **Image workflow:** FLUX.2 Klein 4B FP8; 512×512, four steps, c1; decodable
  PNG in 9.859 seconds; peak 12,919 MiB from a 943 MiB worker baseline.
- **Video workflow:** Wan2.2 TI2V 5B FP16; 512×288, 17 frames, eight steps,
  16 fps, c1; decodable 1.0625-second H.264 MP4 in 9.092 seconds; peak
  18,263 MiB from a clean 943 MiB worker baseline.
- **Runtime:** ComfyUI v0.33.4 at `7a131a3a`, CUDA 13.0, PyTorch
  2.13.0+cu130, digest-pinned base and exact custom-node revisions.
- **Measurement path:** direct loopback managed worker, qualification-only,
  asynchronous durable jobs; no router route or agent client involved.
- **Decision:** functional and capacity evidence passes; both workflows remain
  unavailable and `no-promotion` until independent perceptual quality review.
- **Restoration:** final managed state is worker absent, zero reservation
  committed, and 448 MiB host GPU usage.
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-08-28-comfyui-media-qualification/>

## X / short post

```text
RTX 5090 ComfyUI: FLUX.2 Klein 512px PNG 9.86s/12,919 MiB peak; Wan2.2 17-frame H.264 9.09s/18,263 MiB. Functional only; quality/promotion open. https://fakoli.github.io/anvil-serving/findings/2026-08-28-comfyui-media-qualification/
```

## Reddit

```text
Local FLUX.2 Klein and Wan2.2 qualification on an RTX 5090 via ComfyUI
```

```markdown
I qualified two pinned ComfyUI workflows locally on one RTX 5090 through a
managed, asynchronous job path.

- FLUX.2 Klein 4B FP8: 512×512 PNG, four steps, 9.859 seconds, 12,919 MiB peak
- Wan2.2 TI2V 5B: 512×288, 17-frame H.264 MP4, eight steps, 9.092 seconds,
  18,263 MiB peak from a clean worker baseline
- Both artifacts decoded with the requested dimensions/frame metadata
- The worker was stopped afterward and GPU use returned to 448 MiB

These are single cold c1 functional/capacity runs, not a quality ranking or a
throughput distribution. The media bytes and prompt text are not published,
independent perceptual review remains open, and neither workflow was promoted
or routed.

Full identities, failures, and sanitized evidence:
https://fakoli.github.io/anvil-serving/findings/2026-08-28-comfyui-media-qualification/
```

## Screenshot alt text

Result card for local ComfyUI qualification on one RTX 5090. FLUX.2 Klein
produced a decodable 512-by-512 PNG in 9.859 seconds at 12,919 MiB peak GPU
memory. Wan2.2 produced a decodable 17-frame, 512-by-288 H.264 MP4 in 9.092
seconds at 18,263 MiB peak. A caveat says perceptual quality is unreviewed and
neither workflow was promoted.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| FLUX.2 Klein decodable PNG in 9.859 seconds at 12,919 MiB peak | one 512×512, four-step, c1 cold qualification | [`image-qualification.json`](image-qualification.json) |
| Wan2.2 decodable 17-frame H.264 in 9.092 seconds at 18,263 MiB peak | one clean-baseline 512×288, eight-step, c1 cold qualification | [`video-qualification.json`](video-qualification.json) |
| Final worker absent and GPU baseline restored to 448 MiB | managed stop/remove after all runs | [`rollback.json`](rollback.json) |
| No quality approval or promotion | independent perceptual review not performed; descriptors remain unavailable | [Finding decision boundary](../2026-08-28-comfyui-media-qualification.md#decision-boundary) |
