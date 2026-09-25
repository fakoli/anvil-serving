# Swift-1.5 NInfer on one RTX 5090: bounded local result

<!-- benchmark-publication-summary/v1 -->

This copy is derivative. The [dated finding](../2026-09-25-swift15-ninfer-5090-promotion.md)
and [native artifacts](README.md) control every claim.

## Canonical facts

- **Model:** `kaushikvira/Qwen3.8-27B-swift15-nvfp4full-dflash2-NInfer-v3@ff891a1130adfcf46bf745e06f0fa3dcde2e8c82`; served as `qwen38-swift15-ninfer-dflash2-c4-262k`.
- **Runtime:** NInfer `bace20dc70249eed6402b66d4852c6c3f9612905`; image `sha256:59df48dd99f0d177b73de57ba88eb6457ed7e4b0c5771c45e8976adcd9f5ef67`.
- **Setup:** single RTX 5090 32 GB, Windows/WSL2, all-NVFP4 W4A4, K8V4, DFlash2 K7, vision, 16K default thinking budget, 262,144 context, C4 configured, 450 W cap. The [managed recipe](../../../configs/qwen38-swift15-ninfer-dflash2-rtx5090-baked.toml) uses 2 GiB Host KV because the card's 48 GiB Host KV exceeds the host's physical RAM.
- **Measurement path:** post-promotion direct capacity with warm server and unique request prefixes; routed native context, agentic, and client acceptance.
- **Capacity:** 4K/C1 100/100 completions, 0.31 s mean visible TTFT, 328.21 aggregate visible output tok/s; 4K/C4 100/100, 0.43 s, 614.85 aggregate tok/s. Both are *descriptive* because exact 256-word adherence was zero.
- **Capability:** routed exact-marker context 18/18 through 257,898 actual prompt tokens with 4,096 output reserve; agentic scout 17/18; frozen official SWE Verified 5/5; pinned image/OCR 12/12.
- **Failure:** 128K/C4 completed 5/10; five HTTP 503 serving queue timeouts. This profile does not qualify four concurrent long windows.
- **Decision:** human-authorized current secondary route; the failed 128K/C4 cell, strict-output failure, and absent matched publisher reproduction remain explicit boundaries.

## Short post draft

Local Swift-1.5 NVFP4+DFlash2 on one RTX 5090: routed context 18/18 through 257,898 actual tokens; agentic 17/18; official SWE Verified 5/5; image/OCR 12/12. 128K/C4 failed 5/10 with queue timeouts. Capacity rates are descriptive. [Evidence](../2026-09-25-swift15-ninfer-5090-promotion.md).

This draft has not been posted externally. Recount its final URL and the target site's limits before use.

## Reddit draft

**Title:** Local Swift-1.5 NVFP4+DFlash2 on one RTX 5090: 262K routed context, C4 limit exposed

I served the pinned Swift-1.5 Qwen3.8 27B artifact with NInfer on one RTX 5090 at 262,144 configured tokens, K8V4, DFlash2 K7, vision, and four-request admission. I used 2 GiB Host KV because 48 GiB cannot fit the host RAM.

After promotion, routed exact-marker retrieval passed 18/18 through 257,898 actual prompt tokens. The native agentic scout passed 17/18, frozen official SWE-bench Verified resolved 5/5, and the pinned image/OCR corpus passed 12/12. Short-prompt C1 and C4 capacity completed 100/100 each, but those rates are descriptive because responses missed the exact word target. At 128K/C4, five of ten requests hit engine queue timeouts. [Full methods and raw artifacts](../2026-09-25-swift15-ninfer-5090-promotion.md).

This draft has not been posted externally.

## Screenshot alt text

Swift-1.5 Qwen3.8 27B NInfer on one RTX 5090 at 262K configured context and C4: routed retrieval passed 18 of 18 through 257,898 actual tokens; agentic passed 17 of 18; frozen official SWE-bench Verified resolved 5 of 5; image/OCR passed 12 of 12. Five of ten 128K/C4 requests timed out in the serving queue.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 262K selected profile and memory adaptation | Single RTX 5090, pinned baked image, 2 GiB Host KV | [Startup and recipe](README.md#raw-run-evidence); [feasibility finding](../2026-09-25-swift15-ninfer-5090-exact-recipe-feasibility.md) |
| 18/18 routed context | Exact markers, three depths, C1, 4,096 output reserve | [Native context](context-routed-18.json) |
| 17/18 agentic; 12/12 image/OCR | Frozen bounded suites | [Native agentic](agentic-routed-18.json); [native vision](multimodal-computer-use-12.json) |
| Frozen SWE-bench Verified 5/5 | Five official graded and resolved issues, one worker, default thinking | [Native SWE](swe-routed-5.json) |
| Short-prompt rates are descriptive | Unique prefixes, 100 requests each, 0 exact word targets | [4K/C1](capacity-4k-c1-visible-observe.json); [4K/C4](capacity-4k-c4-visible-observe.json) |
| 128K/C4 fails long-window concurrency | Five queue timeouts among ten requests | [Native failed cell](capacity-128k-c4-visible-observe.json); [failure explanation](../2026-09-25-swift15-ninfer-5090-promotion.md#post-promotion-measurements) |
