# Publication summary: GLM-5.3-Flash SGLang 393K promotion

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The
[dated promotion finding](../2026-09-02-glm53-sglang-sm120-393k-promotion.md)
and linked raw qualification artifacts are authoritative.

## Canonical facts

- **Model identity:**
  `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`,
  served as `glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp`
- **Runtime identity:** SGLang rc14 image
  `sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- **Local setup:** two RTX PRO 6000 Blackwell Max-Q cards, exclusive TP=2
  over PCIe/WSL2, ModelOpt W4A16/NVFP4, FP8 KV, adaptive EAGLE `[3,5]`, C1
- **Recipe:** [managed 393K/C1 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml)
- **Measurement path:** warm online direct qualification followed by managed,
  authenticated routed, and real-client acceptance
- **Headline result:** 112.07 decode tok/s at 2,969 prompt tokens and 99.79
  at 304,491 prompt tokens, each p50 of three C1 requests
- **Capability result:** tools 20/20, coding 15/15, image/OCR 12/12,
  endurance 60/60, both thinking contracts, routed aliases, and real
  Pi/OpenClaw/Hermes passed
- **Important caveat:** the 3,072 MiB/card standing reserve is waived for this
  model-only pair; post-qualification and post-client free VRAM were 2,101 and
  2,543 MiB/card respectively; no video or C2 qualification
- **Decision:** human-approved published `current` text/tools/image/OCR
  profile; exact 524K EXL3/DFlash2 service retained as rollback
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-09-02-glm53-sglang-sm120-393k-promotion/>
- **Artifact set:** [`artifact-manifest.json`](artifact-manifest.json) and
  [`README.md`](README.md)

## X / short post

Recount immediately before posting.

```text
GLM-5.3-Flash W4A16, 2x RTX PRO 6000 TP=2/C1: 112.1 tok/s at 4K, 99.8 at 304K; tools 20/20, image/OCR 12/12. 393K promoted. https://fakoli.github.io/anvil-serving/findings/2026-09-02-glm53-sglang-sm120-393k-promotion/
```

## Reddit

Check the target community's current rules before posting.

```text
GLM-5.3-Flash W4A16 on dual RTX PRO 6000: 393K/C1, 112 tok/s, clients pass
```

```markdown
I qualified and promoted the pinned ormandj GLM-5.3-Flash W4A16/NVFP4
checkpoint on two RTX PRO 6000 Blackwell Max-Q cards using exclusive TP=2 over
PCIe/WSL2, SGLang rc14, FP8 KV, adaptive EAGLE [3,5], and concurrency one.

Headline results:

- 4K target / 2,969 actual prompt tokens: 112.07 decode tok/s
- 380K target / 304,491 actual prompt tokens: 99.79 decode tok/s
- tools 20/20, coding 15/15, image/OCR 12/12, endurance 60/60
- managed/routed gates and real Pi, OpenClaw, and Hermes acceptance passed

The 3 GiB-per-card campaign reserve was explicitly waived because this GPU
pair runs only model workloads. Free VRAM was 2,101 MiB/card after the direct
qualification and 2,543 MiB/card after client acceptance, with no OOM,
restart, crash, or shared-memory residue. This is a bounded local C1 result,
not a universal model ranking or a C2/video qualification.

Full methodology, failures, and evidence:
https://fakoli.github.io/anvil-serving/findings/2026-09-02-glm53-sglang-sm120-393k-promotion/
```

## Screenshot alt text

Benchmark result card for pinned GLM-5.3-Flash W4A16/NVFP4 on two RTX PRO
6000 Blackwell Max-Q GPUs at TP=2, 393,216 context, and concurrency one. It
reports 112.07 decode tokens per second at 2,969 prompt tokens, 99.79 at
304,491 prompt tokens, full tool/image/OCR/endurance passes, and real-client
acceptance. A caveat states that the 3 GiB reserve was waived for model-only
GPUs and video/C2 are not qualified.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 112.07 decode tok/s | 2,969 prompt tokens; C1; p50 of three | [promotion finding](../2026-09-02-glm53-sglang-sm120-393k-promotion.md#result-card) · [`safe393k-capacity-c1-4k-r3.json`](safe393k-capacity-c1-4k-r3.json) |
| 99.79 decode tok/s | 304,491 prompt tokens; C1; p50 of three | [promotion finding](../2026-09-02-glm53-sglang-sm120-393k-promotion.md#result-card) · [`safe393k-capacity-c1-380k-r3.json`](safe393k-capacity-c1-380k-r3.json) |
| Tools, coding, media, and endurance passed | 20/20, 15/15, 12/12, and 60/60 | [promotion finding](../2026-09-02-glm53-sglang-sm120-393k-promotion.md#real-client-acceptance) · [`safe393k-preflight-all-low.json`](safe393k-preflight-all-low.json) · [`safe393k-quality-coding-agent-v2-disabled-r3-visible2048.json`](safe393k-quality-coding-agent-v2-disabled-r3-visible2048.json) · [`safe393k-multimodal-image-c1.json`](safe393k-multimodal-image-c1.json) · [`safe393k-endurance-c1-4k-r60.json`](safe393k-endurance-c1-4k-r60.json) |
| Reserve was waived without changing the physical measurement | default 3,072 MiB/card; effective 0; 2,101 MiB/card after qualification | [VRAM policy section](../2026-09-02-glm53-sglang-sm120-393k-promotion.md#vram-policy-reclassification) · [`vram-policy-reclassification.json`](vram-policy-reclassification.json) |
