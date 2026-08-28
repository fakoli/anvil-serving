# Publication summary: Qwen3.8 27B NVFP4 RTX 5090 128K result

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md) and
linked raw artifacts are authoritative.

## Canonical facts

- **Model identity:**
  `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`,
  served as `qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm`
- **Runtime identity:** SGLang
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`, image
  `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`
- **Local setup:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120; isolated
  Windows 11 / Docker Desktop / WSL2 lane; ModelOpt NVFP4/FP8 weights, FP8
  E4M3 KV, 131,072 context, c1, MTP and thinking disabled
- **Recipe:** [managed 128K multimodal recipe at `85b21147`](https://github.com/fakoli/anvil-serving/blob/85b2114745a1a66f30252876ab528d49f12e8a73/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm-recipe.toml)
- **Measurement path:** direct online managed endpoint; retained artifacts do
  not classify individual requests as cold or warm
- **Headline result:** exact retrieval marker at 119,675 prompt tokens plus 14
  completion tokens in 29.811 seconds; tool calls passed 20/20
- **Capability result:** established multimodal corpus passed 30/30 and the
  corrected eight-image/two-video boundary corpus passed 4/4; direct
  image/mixed/video latency p50 was 0.882 / 1.535 / 1.602 seconds
- **Important caveat:** evidence is direct and c1 with no controlled decode-rate
  or routed acceptance; the retained invalid-expectation artifact scored 2/4
  because both image rubrics named labels absent from their source assets
- **Decision:** retain direct 128K `challenger`, `no-promotion`, with the exact
  64K recipe as rollback; no route or deployment state changed
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k/>

## X / short post

225 literal characters including the URL as checked on 2026-08-27. Recount
immediately before posting.

```text
Local Qwen3.8 27B NVFP4, RTX 5090 128K/c1: 119,675-token retrieval; vision 30/30; media bounds 4/4. Direct only, no promotion. https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k/
```

## Reddit

The title is 71 literal characters. Check the target community's current rules
before posting.

```text
Local Qwen3.8 27B NVFP4 on RTX 5090: 128K c1 and media-count boundaries
```

```markdown
I tested RadixArk Qwen3.8 27B NVFP4 locally on one RTX 5090 using SGLang,
FP8 E4M3 KV, a 131,072-token window, and concurrency one.

Headline results:

- Long-context retrieval returned the exact marker at 119,675 prompt tokens
  plus 14 completion tokens in 29.811 seconds
- Tool calls passed 20/20
- The established multimodal corpus passed 30/30: image 12/12, mixed 4/4,
  video 14/14
- The corrected media-count corpus passed 4/4: two eight-image and two
  two-video attempts
- Image/mixed/video latency p50 was 0.882/1.535/1.602 seconds

This is direct c1 evidence with no controlled decode-rate or routed-acceptance
result. An earlier artifact is retained at 2/4 because its image rubric named
labels absent from the source assets. This is a bounded local challenger, not
a universal model ranking or a promotion.

Full methodology, failures, and raw artifacts:
https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k/

What matches or differs on your hardware?
```

## Screenshot alt text

Benchmark result card for RadixArk Qwen3.8 27B NVFP4 on one RTX 5090 at 128K
context and concurrency one. It reports a 119,675-token retrieval pass, 30 of
30 established multimodal attempts, and 4 of 4 corrected media-count boundary
attempts. A caveat notes the evidence is direct c1 and retains an earlier
invalid-expectation artifact at 2 of 4.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Retrieval pass at 119,675 prompt tokens in 29.811 seconds | one direct c1 request; exact marker and `stop` | [Finding gates](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md#gates-and-measurements) · [`preflight-needle-119675-tokens.json`](preflight-needle-119675-tokens.json) |
| Established multimodal 30/30 | direct c1 corpus; image 12/12, mixed 4/4, video 14/14 | [Finding gates](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md#gates-and-measurements) · [`multimodal-c1.json`](multimodal-c1.json) |
| Corrected media-count boundary 4/4 | two eight-image and two two-video attempts at c1 | [Finding gates](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md#gates-and-measurements) · [`count-boundaries-c1.json`](count-boundaries-c1.json) |
| Retained invalid-expectation artifact 2/4 | video 2/2; image 0/2 because the rubric named absent source labels | [Finding failure record](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md#failure-record-and-caveats) · [`count-boundaries-invalid-expectations.json`](count-boundaries-invalid-expectations.json) |
| Direct-only challenger with no promotion | c1; no decode-rate or routed acceptance | [Finding decision boundary](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md#decision-boundary) · [evidence manifest](README.md) |
