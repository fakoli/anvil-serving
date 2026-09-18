# Publication summary: GLM EXL3 eight-image acceptance

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

Local functional acceptance: dual RTX PRO 6000 Blackwell Max-Q, pinned 4-bpw EXL3, FP8 DS-MLA KV, TP2/EP2/DCP2, speculation off, configured 327,680 tokens/C4, **eight images/request**, no video. The [current recipe](eight-images/recipe.toml) reconstructs r9. Four eight-card requests, one high-resolution eight-image request and real Pi acceptance passed. The large request contained 63,456 input tokens and completed in 28.17 seconds. Six direct preflight checks passed. No speed ranking, broad visual-quality comparison or simultaneous eight-image soak is claimed. The prior one-image profile is the rollback.

Model: `brandonmusic/GLM-5.3-Flash-tr3-4bpw@a5fee929cf4888b1824323e33e8a19b60129e025`. Image: `sha256:0f1cdcc8891f1cc3a444121eb61d366289a1cbba285f0892dcbb24bc94961692`. The inherited runtime build metadata and its attestation limitation are in the [finding](../2026-09-18-glm53-vision.md). Direct and routed online requests followed cold startup, then warmed kernels and mixed image-cache state. [Manifest](artifact-manifest.json) · [Evidence index](README.md).

## X / short post

```text
Local GLM EXL3 on dual RTX PRO 6000 Max-Q passed five eight-image tests plus Pi acceptance. One 63K-token image batch took 28.17s. No concurrency-soak claim.
https://fakoli.github.io/anvil-serving/findings/2026-09-18-glm53-vision/
```

## Reddit

Title: GLM EXL3 on dual RTX PRO 6000 Max-Q: eight-image functional acceptance

Pinned 4-bpw EXL3 with FP8 KV and speculation disabled retained its configured 327K window. Four eight-card comparison requests passed in forward/reverse order. Eight 2520×2520 cards also passed: 63,456 input tokens, 28.17 seconds for that one request. A real Pi request read all eight images correctly, and coding/JSON/tool regressions passed. The limit is eight images/request, no video. This is synthetic functional acceptance, not a general performance or vision-quality ranking; simultaneous eight-image requests were not soaked. Full method, earlier negative results and raw evidence are in the linked finding.

## Screenshot alt text

GLM EXL3 on two RTX PRO 6000 Max-Q GPUs passes four ordinary eight-image requests, one high-resolution eight-image request and real Pi acceptance. Configured 327K context, FP8 KV, speculation off; no concurrent eight-image soak.

## Claim ledger

| Claim | Conditions | Evidence |
|---|---|---|
| Four ordinary eight-image requests passed | Forward/reverse order, two repetitions, C1 | [Native result](eight-images/eight-images.json) |
| Large eight-image request passed in 28.17 seconds | One request, 2520×2520 each, 63,456 actual prompt tokens | [Native result](eight-images/eight-images-large.json) |
| Real Pi accepts eight images | Fresh process through the normal router | [Pi response](eight-images/pi-eight-images.txt) |
| Six direct preflight checks passed | Includes four tool calls | [Preflight](eight-images/preflight.json) |
| Eight/request; 327K configured | C4 scheduler, no simultaneous-eight-image soak | [Recipe](eight-images/recipe.toml), [limits](eight-images/summary.json) |

The earlier one-image recipe passed 10/10 synthetic image attempts. Its 259,922-token retrieval refused once and passed on retry after reported overlapping traffic; causation is unproven. Those results remain [separate dated evidence](../2026-09-18-glm53-vision.md#initial-one-image-results-and-limitations), not a fresh long-context gate for r9.
