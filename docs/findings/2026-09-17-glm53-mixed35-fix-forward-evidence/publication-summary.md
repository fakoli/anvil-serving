<!-- benchmark-publication-summary/v1 -->

# GLM mixed 3.5-bpw loader recovery: publication summary

The loader repair made the candidate runnable under a 60 GiB RAM limit.
C1/C4 preflight and repeated bounded C4 quality checks passed. Retrieval
succeeded at 216,307 actual input tokens. Strict capacity remained
inconsistent, so the baseline was restored without promotion.

## Copy

Short post (224 characters): GLM mixed 3.5-bpw now runs. C4 quality and 216K-token retrieval passed; strict capacity was inconsistent. Baseline restored, no promotion. https://fakoli.github.io/anvil-serving/findings/2026-09-17-glm53-mixed35-fix-forward/

Reddit title: Repaired GLM mixed 3.5-bpw loader: working C4, no promotion yet

Reddit body: A synchronous R7 weight-transfer patch removed CPU retention.
The candidate passed bounded repeated quality and one 216,307-token retrieval.
Its first strict C4 cell completed 4/4 at 98.17 completion tok/s, including
reasoning; the next completed only 3/4. The baseline also failed strict output
counts, so there is no valid throughput comparison or demonstrated overall win.

Screenshot alt text: Candidate and baseline test table separating quality,
actual context tokens, valid capacity, strict failures, and no-promotion.

## Claim ledger

| Claim | Evidence |
| --- | --- |
| Loader recovery and contained OOM probe | [Containment](containment.json) |
| C4 preflight and repeated bounded quality passed | [Preflight](candidate-c4-preflight.json), [quality](candidate-c4-quality.json) |
| Exact retrieval at 216,307 actual input tokens | [Needle](candidate-needle.json) |
| One valid C4 cell, one failed repeat | [First cell](candidate-c4-capacity-1.json), [second cell](candidate-c4-capacity-2.json) |
| Baseline restored, no promotion | [Restoration](restoration-c4.json), [decision](summary.json) |
