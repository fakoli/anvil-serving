# Publication summary: Huihui Qwen3.8 27B NInfer NVFP4 scout

<!-- benchmark-publication-summary/v1 -->

This derivative copy does not replace the [dated finding](../2026-09-19-qwen38-huihui-ninfer.md) or native evidence.

## Canonical facts

- **Model identity:** `lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4@181446902fc777c479749e98cf2abf2250263a8d`.
- **Runtime identity:** NInfer `a99407c63fc5bbd25d9fb597cbb8ab352bdb01ef`; exact CUDA 13.1.2 image digest in [configuration](configuration.json).
- **Local setup:** RTX 5090, isolated Windows/WSL2 direct lane, NVFP4 no-spec, 8K/C1.
- **Headline result:** strict tools failed 0/3 at default and low-effort diagnostics; default diff also failed 0/3.
- **Capability result:** corrected `--vision` image-only corpus passed 12/12; earlier 0/12 `vision_disabled` is retained.
- **Important caveat:** no matched speed, footprint, or context comparison; immutable runtime recreation is unproven. Managed restoration is verified.
- **Decision:** `rejected`, `no-promotion`, `promoted=false`.
- **Artifact set:** [manifest](artifact-manifest.json) and [evidence index](README.md).

## X / short post

```text
RTX 5090 Huihui Qwen3.8 NInfer 8K profile: corrected vision passed 12/12, but strict tools were 0/3 at both tested settings. Exact tested profile rejected; no promotion or performance claim. Evidence: docs/findings/2026-09-19-qwen38-huihui-ninfer.md
```

## Reddit

```text
RTX 5090 Huihui Qwen3.8 NInfer scout: vision passes, strict tools fail
```

```markdown
Pinned Huihui Qwen3.8 NInfer NVFP4 at 8K/C1 passed a corrected 12/12 image-only subset, while strict tools failed 0/3 under both tested settings. This is a bounded local scout, not a speed, footprint, or model-ranking claim. The exact tested profile is rejected with no promotion.
```

## Screenshot alt text

RTX 5090 Huihui Qwen3.8 NInfer NVFP4 8K/C1 scout: corrected vision passed 12 of 12 images, while strict tools failed all three attempts at both settings; no promotion.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Strict tools failed 0/3 at both settings | 8K/C1, three attempts each | [default](quality-nospec-8k.json); [low](quality-nospec-reasoning-low.json) |
| Vision-enabled image subset passed 12/12 | six cases, two attempts each | [artifact](multimodal-nospec-8k-vision.json) |
| No performance or footprint claim | no matched capacity artifacts | [coverage](coverage-and-gaps.md) |
