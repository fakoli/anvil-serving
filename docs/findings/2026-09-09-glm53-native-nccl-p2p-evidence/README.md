# GLM-5.3-Flash native NCCL P2P transport A/B evidence

This directory contains the sanitized public evidence for
[`2026-09-09-glm53-native-nccl-p2p`](../2026-09-09-glm53-native-nccl-p2p.md).

## Fixed campaign boundary

- **Model:** `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO` at
  `c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- **Runtime:** `ghcr.io/ormandj/sglang-glm53-flash-sm120@sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- **Measured configuration:** native Linux kernel `7.0.0-31`, driver
  `595.91.07`, two RTX PRO 6000 Blackwell Max-Q GPUs, TP=2, 393,216 tokens,
  C1, and adaptive MTP fixed.
- **Only intended A/B delta:** `NCCL_P2P_DISABLE=1` versus
  `NCCL_P2P_DISABLE=0`; `NCCL_CUMEM_ENABLE=0`, IOMMU-off state, and ACS state
  are fixed.
- **Capacity protocol:** 4K/120K target contexts at n=12/3 for comparative
  performance, with unique prompt cache mode, request canaries, strict
  32-code-word output, and a 512-token maximum. The earlier 128-word scout
  and 380K n=1 capacity attempt remain retained negative evidence; the latter
  reached 304,588 actual prompt tokens but returned 33 rather than 32 code
  words and is not performance-eligible. Matched 380K target retrieval and
  long-tool assertions are separate correctness gates.

## Finalization and validation

The artifact manifest retains all ten required roles. Native request artifacts
remain authoritative. Public files redact private identifiers while preserving
native schemas and a redaction record. The graph and manifest helpers each
produced byte-identical results across two runs. The 19 focused publication
tests, tracked Markdown link check and strict MkDocs build passed. The strict
build used the repository's docs requirements in an isolated tool environment.

The authorized native GLM recipe retains P2P enabled. See the [decision summary](summary.json), [publication summary](publication-summary.md), [comparison](comparison.json), and [chart](benchmark-matrix.svg). The two 380K strict-output failures and the raw diagnostic marker failure remain retained limitations.
