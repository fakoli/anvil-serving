# Publication summary: GLM-5.3-Flash K3/DFlash2 on dual RTX PRO 6000

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The linked dated finding and raw
artifacts are authoritative.

## Canonical facts

- **Model identity:** `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1@319d66a8b53092b491f698440ecea781e4ddd4e4`; served as `glm53-flash-exl3-k3-dflash2-k5-fp8-tp2-1m-vision`
- **Runtime identity:** vLLM `0.1.dev20051+g487ecf187`; image `sha256:001a45bd71bcf908a8c07459570bdb8c5e0a205d085f29ac7f3201529fa3eb75`
- **Local setup:** 2x RTX PRO 6000 Blackwell Max-Q, 96 GB each, WSL2/Docker Desktop, PCIe without NVLink, TP=2/DCP=2, EXL3 K3 target, FP8 DS-MLA KV, DFlash2 BF16 K5
- **Recipe:** [managed K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-fp8-1m-vision-sm120-tp2-wsl2-recipe.toml)
- **Measurement path:** warm direct and authenticated routed online endpoints; real Hermes/OpenClaw/Pi client acceptance
- **Headline result:** 82.1 tok/s at 4K, 67.4 at 131K, and 67.9 at 240K, all c1/five requests; exact retrieval at a 950K target
- **Capability result:** tools 20/20, image corpus 12/12, bounded deterministic quality 12/12, direct and routed functional suites all-pass
- **Important caveat:** DFlash2 is CC-BY-NC-ND-4.0; no video; WSL2 uses PyNCCL instead of upstream peer-IPC collectives; c16 does not mean sixteen full 1M windows
- **Decision:** human-authorized `current` one-week GLM default; K3 retained as alternate; batch4096 rejected
- **Canonical evidence:** https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md

## X / short post

Preferred project limit: 260 literal characters including the URL. Recount
immediately before posting.

```text
LOCAL: GLM-5.3-Flash K3+DFlash2 K5 on 2x RTX PRO 6000 Max-Q: 82 tok/s at 4K, 68 at 240K, exact 950K retrieval, image/OCR 12/12. WSL2/PyNCCL; no video; noncommercial. https://github.com/fakoli/anvil-serving
```

## Reddit

Preferred title limit: 120 characters. Check the target community's current
rules before posting.

```text
Local GLM-5.3-Flash K3+DFlash2 on 2x RTX PRO 6000 Max-Q: 1M context, image/OCR, 82 tok/s
```

```markdown
I tested the exact `wrldsuksgo2mars/GLM-5.3-Flash-EXL3-K3-v1` target with the
`incoai/GLM-5.3-Flash-DFlash2` K5 draft on two 96 GB RTX PRO 6000 Blackwell
Max-Q cards under Docker Desktop/WSL2.

- TP=2/DCP=2, 1,048,576 configured tokens, FP8 DS-MLA target KV
- 82.1 tok/s at 4K, 67.4 at 131K, and 67.9 at 240K, c1/five requests
- exact retrieval at a 950K target
- tools 20/20 and image/OCR corpus 12/12
- K3 and a 4,096-token scheduler chunk were tested; K5/2,048 won the default
- four Hermes profiles, OpenClaw's running gateway, and Pi's normal PTY path passed

The main caveats: WSL2 required a PyNCCL fallback, video is unsupported, c16 is
not sixteen simultaneous 1M prompts, and the DFlash2 draft is
CC-BY-NC-ND-4.0/noncommercial without separate permission. These are bounded
local results, not a universal model ranking.

Full methodology, failures, and raw artifacts:
https://github.com/fakoli/anvil-serving/blob/main/docs/findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md

What matched or differed on your hardware?
```

## Screenshot alt text

A benchmark table for GLM-5.3-Flash EXL3 K3 with DFlash2 K5 on two RTX PRO
6000 Blackwell Max-Q GPUs under WSL2. It shows 82.1 output tokens per second at
4K, 67.4 at 131K, 67.9 at 240K, exact 950K-target retrieval, tools 20/20, and
image/OCR 12/12. A caveat notes PyNCCL fallback, no video, and the DFlash2
noncommercial license.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 82.1 tok/s at 4K and 67.9 at 240K | K5, c1, five requests, local dual-Max-Q/WSL2 | [finding](../2026-08-30-glm53-k3-dflash2-1m-optimization.md#matched-single-request-depth-sweep); [4K artifact](challenger-k3-dflash2-k5-1m-c1-ctx4096-r5.json); [240K artifact](challenger-k3-dflash2-k5-1m-c1-ctx240000-r5.json) |
| Exact 950K-target retrieval | one K5 request, low reasoning control | [finding](../2026-08-30-glm53-k3-dflash2-1m-optimization.md#concurrency-and-capacity); [artifact](challenger-k3-dflash2-k5-1m-needle-950k-low.json) |
| Tools 20/20 and image/OCR 12/12 | direct K5 functional and image-only corpora | [finding](../2026-08-30-glm53-k3-dflash2-1m-optimization.md#functional-quality-and-client-acceptance); [preflight](challenger-k3-dflash2-k5-1m-preflight-full-low-250k.json); [image corpus](challenger-k3-dflash2-k5-1m-image-corpus-c1-r2.json) |
| Batch4096 rejected | matched 4K c1 and C16 diagnostics | [finding](../2026-08-30-glm53-k3-dflash2-1m-optimization.md#outcome-and-decision); [4K artifact](challenger-k3-dflash2-k5-bt4096-1m-c1-ctx4096-r5.json); [C16 artifact](challenger-k3-dflash2-k5-bt4096-1m-c16-ctx4096-r32.json) |
