# Publication summary: Qwen3.8 27B NVFP4 RTX 5090 64K result

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md) and linked
raw artifacts are authoritative.

## Canonical facts

- **Model identity:**
  `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`,
  served as `qwen38-27b-radixark-nvfp4-sglang-rtx5090-64k-mm`
- **Runtime identity:** SGLang
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`, image
  `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`
- **Local setup:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120; isolated
  Windows 11 / Docker Desktop / WSL2 lane; ModelOpt NVFP4/FP8 weights, FP8
  E4M3 KV, 65,536 context, c1, MTP and thinking disabled
- **Recipe:** [managed 64K multimodal recipe at `85b21147`](https://github.com/fakoli/anvil-serving/blob/85b2114745a1a66f30252876ab528d49f12e8a73/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-64k-mm-recipe.toml)
- **Measurement path:** direct online managed endpoint; retained artifacts do
  not classify individual requests as cold or warm
- **Headline result:** approximately 60,000-token retrieval marker returned
  with `stop`; tool calls passed 20/20
- **Capability result:** deterministic multimodal corpus passed 30/30: image
  12/12, mixed 4/4, video 14/14; latency p50 was 0.886 / 1.548 / 1.648 seconds
- **Important caveat:** no controlled decode-rate benchmark, c2+, routed
  acceptance, broad GUI-grounding benchmark, or action loop was run
- **Decision:** `challenger`, `no-promotion`; no route, deployment, or serving
  state changed
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090/>

## X / short post

223 literal characters including the URL as checked on 2026-08-27. Recount
immediately before posting.

```text
Local Qwen3.8 27B NVFP4, RTX 5090 64K/c1: ~60K retrieval pass, tools 20/20, multimodal 30/30. Direct only; no decode/routed test. https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090/
```

## Reddit

The title is 77 literal characters. Check the target community's current rules
before posting.

```text
Local Qwen3.8 27B NVFP4 on RTX 5090: 64K c1 retrieval, tools, and vision pass
```

```markdown
I tested RadixArk Qwen3.8 27B NVFP4 locally on one RTX 5090 using SGLang,
FP8 E4M3 KV, a 65,536-token window, and concurrency one.

Headline results:

- Approximately 60,000-token retrieval returned the exact marker with `stop`
- Tool calls passed 20/20
- The deterministic multimodal corpus passed 30/30: image 12/12, mixed 4/4,
  video 14/14
- Image/mixed/video latency p50 was 0.886/1.548/1.648 seconds

No controlled decode-rate benchmark, c2+, routed acceptance, broad GUI
grounding, or action loop was run. This is a bounded direct local result, not a
universal model ranking or a promotion.

Full methodology, failures, and raw artifacts:
https://fakoli.github.io/anvil-serving/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090/

What matches or differs on your hardware?
```

## Screenshot alt text

Benchmark result card for RadixArk Qwen3.8 27B NVFP4 on one RTX 5090 at 64K
context and concurrency one. It reports an approximately 60K retrieval pass,
20 of 20 tool calls, and 30 of 30 multimodal attempts. A caveat notes that no
controlled decode-rate, higher-concurrency, routed, or action-loop test ran.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Approximately 60K retrieval pass and tools 20/20 | direct preflight, c1, exact marker and accepted finish states | [Finding gates](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md#gates-and-measurements) · [`preflight-functional-60k.json`](preflight-functional-60k.json) |
| Multimodal 30/30 | direct c1 deterministic corpus; image 12/12, mixed 4/4, video 14/14 | [Finding gates](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md#gates-and-measurements) · [`multimodal-c1.json`](multimodal-c1.json) |
| Modality p50 0.886 / 1.548 / 1.648 seconds | image / mixed / video across the same 30 attempts | [Finding measurements](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md#gates-and-measurements) · [`multimodal-c1.json`](multimodal-c1.json) |
| Direct-only challenger with no promotion | c1; no decode-rate, routed acceptance, or action loop | [Finding caveats](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md#failures-and-caveats) · [evidence manifest](README.md) |
