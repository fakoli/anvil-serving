# Publication summary: Huihui Qwen3.8 64K C1

<!-- benchmark-publication-summary/v1 -->

This copy derives from the linked native artifacts and dated finding.

## Canonical facts

- **Model:** `lyf/Qwen3.8-27B-Huihui-Abliterated-NInfer-NVFP4@181446902fc777c479749e98cf2abf2250263a8d`; served `qwen38-huihui-ninfer-schemafix-mtp3-64k`.
- **Runtime:** NInfer `70434721b1ae29d0616f3de9b376c8a4d91590b5`; image `sha256:44377d56374ed4b832c2ca6e9265da492cf2ad79a297008bae92f442d27edfb6`.
- **Setup:** one RTX 5090, Windows/WSL2, NVFP4/mixed FP8 weights, INT8 group-64 KV, MTP3, no thinking, vision, 65,536 context tokens, C1.
- **Recipe:** [exact sanitized managed recipe](recipe-64k-sanitized.toml), bound to [private launch hashes](identity-receipt.json).
- **Measurement path:** direct online capacity with unique prompts; native routed protocol and Hermes checks after propagation. No kernel-only or matched 64K no-spec measurement.
- **Headline:** descriptive, canary-free C1 mean decode rates of 182.1 tokens/s for 12 short requests and 167.1 for six long requests. These are not finalist comparative performance claims.
- **Capabilities:** direct preflight 7/7, routed preflight 6/6, repeated bounded quality, vision 12/12, Hermes 9/9; quality request had 62,455 actual prompt tokens.
- **Caveats:** post-workload memory 24,224 MiB is higher than the historical GGUF control; no endurance, transient peak or interactive browser acceptance claim.
- **Decision:** `current`, separately authorized promotion; retain exact 32K rollback.
- **Canonical evidence:** [dated finding](../2026-09-19-qwen38-huihui-64k.md).
- **Artifact set:** [manifest](artifact-manifest.json), [index](README.md).

## Claim ledger

| Claim | Conditions | Evidence |
|---|---|---|
| Bounded protocol and quality passed | Pinned 64K C1 profile, repeated core/session and one long context request | [preflight](preflight-c1.json), [quality](quality.json), [routed preflight](router-preflight.json) |
| Vision 12/12 | Hashed synthetic image corpus, two repetitions per case | [vision](vision.json) |
| Descriptive decode 182.1 / 167.1 tokens/s | C1, unique prompts, 32 exact words, n12/n6, no canaries | [short](capacity-short.json), [long](capacity-long.json) |
| Hermes reached the local secondary | Native `anvil` provider, exact alias/provider, tool-result continuation | [native client receipt](hermes-native.json) |
| GPU use 24,224 MiB | After retained workloads, not a transient peak | [memory](memory-post-workload.json) |
| Oversized default preflight failed | Mistaken 128K/C20 defaults, then corrected to in-range C1 | [failure](preflight-default-128k-failure.json), [friction](friction-log.md) |

No social post was requested or sent.
