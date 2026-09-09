# Publication summary: GLM-5.3-Flash native NCCL P2P transport A/B

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Model identity:** `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- **Runtime identity:** digest-pinned SGLang image `0c063795`; TP=2, 393,216 tokens, C1.
- **Local setup:** two RTX PRO 6000 Blackwell Max-Q GPUs; only `NCCL_P2P_DISABLE=1` to `0` changed.
- **Headline result:** 4K n=12: median TTFT -9.0%, E2E -7.4%, decode +2.1%. 120K n=3: TTFT -8.9%, E2E -8.9%, decode +1.4%.
- **Important caveat:** fixed 32-word output, small populations, partial cache metadata, and no high-context performance result.
- **Decision:** user-authorized native GLM default retains P2P enabled; no global NCCL or client-contract change.
- **Canonical evidence:** [dated finding](../2026-09-09-glm53-native-nccl-p2p.md) and [artifact manifest](artifact-manifest.json).

## Screenshot alt text

Chart comparing P2P-disabled and P2P-enabled native GLM-5.3-Flash TP=2 C1 cells at 4K and 120K. P2P enabled has lower median TTFT and E2E; the chart does not include the failed 380K strict-output cells.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| P2P enabled lowered median latency in the eligible cells | 4K n=12 and 120K n=3, strict 32 words, unique-prefix C1 | [comparison](comparison.json) |
| The native default retains P2P enabled | authorized bounded recipe decision after routed checks | [restoration](restoration.json) |
| No high-context performance claim | both 380K strict cells returned 33 rather than 32 words | [coverage](coverage-and-gaps.md) |
