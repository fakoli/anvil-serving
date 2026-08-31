# Publication summary: GLM-5.3-Flash 524K xgrammar qualification

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The linked dated finding and raw
artifacts are authoritative.

## Canonical facts

- **Model identity:** `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4`; served as `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-524k-vision-xgfix`
- **Runtime identity:** vLLM source `487ecf187`; image `anvil-glm53-xgrammar@sha256:4909e318ba1348a179824e210f90c268d6fc68e8b4e514af4782e26e6a1e5939`
- **Local setup:** 2x RTX PRO 6000 Blackwell Max-Q, 96 GB each, WSL2/Docker Desktop, PCIe without NVLink, TP=2/DCP=2, EXL3 K3 target, FP8 DS-MLA KV, DFlash2 BF16 K5
- **Recipe:** [managed DFlash2 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml) and [matched no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-nospec-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml)
- **Measurement path:** warm direct online endpoint plus authenticated routed OpenClaw, Hermes, and Pi acceptance
- **Headline result:** DFlash2 raised p50 decode from 42.61 to 83.08 tok/s at 4K and from 43.63 to a pooled 69.99 tok/s at 240K
- **Capability result:** C2 nominal 250K completed 2/2; tools 20/20; strict JSON, long-context retrieval, image/OCR, bounded quality, and real clients passed
- **Important caveat:** DFlash2 is CC-BY-NC-ND-4.0; the C2 prompts were 206,630 actual tokens each; configured c16 is not sixteen full 524K windows
- **Decision:** qualified and forward-restored with the 1M profile retained as rollback; promotion remains human-gated
- **Canonical evidence:** https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-31-glm53-xgrammar-524k-qualification.md

## X / short post

Preferred project limit: 260 literal characters including the URL. Recount
immediately before posting.

```text
LOCAL: GLM-5.3-Flash K3+DFlash2 K5 on 2x RTX PRO 6000 Max-Q: 83 tok/s at 4K, 70 at 240K, C2 nominal 250K 2/2, tools 20/20. WSL2; noncommercial. https://github.com/fakoli/anvil-serving
```

## Reddit

Preferred title limit: 120 characters. Check the target community's current
rules before posting.

```text
Local GLM-5.3-Flash K3+DFlash2 on dual RTX PRO 6000: 524K, C2, 83 tok/s at 4K
```

```markdown
I tested the exact `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with the
`incoai/GLM-5.3-Flash-DFlash2` K5 draft on two 96 GB RTX PRO 6000 Blackwell
Max-Q cards under Docker Desktop/WSL2.

- TP=2/DCP=2, 524,288 configured tokens, FP8 DS-MLA target KV
- matched no-spec versus DFlash2 K5 with the same target, runtime, and scheduler
- p50 decode rose from 42.61 to 83.08 tok/s at 4K
- 240K decode rose from 43.63 to 69.99 tok/s across ten DFlash2 requests
- two nominal 250K requests completed 2/2; each prompt was 206,630 actual tokens
- tools 20/20 plus strict JSON, long-context, image/OCR, quality, and real-client gates passed

The important caveats are the DFlash2 draft's noncommercial license boundary,
the difference between nominal and actual C2 prompt tokens, and that configured
c16 does not prove sixteen simultaneous full-window prompts. These are bounded
local results, not a universal model ranking.

Full methodology, failures, and raw artifacts:
https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-31-glm53-xgrammar-524k-qualification.md

What matched or differed on your hardware?
```

## Screenshot alt text

A result card for GLM-5.3-Flash EXL3 K3 with DFlash2 K5 on two RTX PRO 6000
Blackwell Max-Q GPUs under WSL2. It shows 83.08 output tokens per second at 4K,
69.99 at 240K, two of two nominal 250K concurrent requests completed, and tools
20 of 20. Caveats note the noncommercial draft license and the 206,630 actual
tokens in each C2 prompt.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 42.61 to 83.08 tok/s at 4K | matched c1, five requests per arm, local dual-Max-Q/WSL2, p50 | [finding](../2026-08-31-glm53-xgrammar-524k-qualification.md#matched-speculative-decoding-ab); [control](nospec-c1-ctx4096-r5.json); [DFlash2](dflash2-c1-ctx4096-r5.json) |
| 43.63 to 69.99 tok/s at 240K | matched c1; five-request control and ten DFlash2 requests pooled across two retained runs | [finding](../2026-08-31-glm53-xgrammar-524k-qualification.md#matched-speculative-decoding-ab); [control](nospec-c1-ctx240000-r5.json); [DFlash2 run 1](dflash2-c1-ctx240000-r5.json); [DFlash2 run 2](dflash2-c1-ctx240000-r5-repeat2.json) |
| C2 nominal 250K completed 2/2 | two concurrent requests, 206,630 actual prompt tokens each, 8,192-token completion allowance | [finding](../2026-08-31-glm53-xgrammar-524k-qualification.md#feasibility-and-capacity); [DFlash2 C2](dflash2-c2-ctx250000-max8192-r2.json) |
| Tools 20/20 plus functional and quality gates passed | selected DFlash2 text/tool preflight, image/OCR, and bounded quality artifacts | [finding](../2026-08-31-glm53-xgrammar-524k-qualification.md#functional-quality-and-client-gates); [preflight](dflash2-preflight-text-tools-250k.json); [vision/OCR](dflash2-vision-ocr.json); [quality](dflash2-quality-all-high-r3.json) |
