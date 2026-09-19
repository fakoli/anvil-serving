# Publication summary: GLM-5.3-Flash APC

<!-- benchmark-publication-summary/v1 -->

The [finding](../2026-09-19-glm53-apc.md) and [artifact set](artifact-manifest.json)
are authoritative. This copy is ready for review; nothing has been posted.

## Canonical facts

Pinned GLM-5.3-Flash EXL3 4-bpw runs on two RTX PRO 6000 Blackwell Max-Q GPUs
with v84, FP8 MLA KV, TP2/EP2/DCP2, configured 327680 context, C4 and no speculation.
Shared-prefix mean visible TTFT fell 67.84%, request throughput rose 3.17 times,
and actual cache reuse was 75.58%. Fresh-prefix request throughput fell 2.39%.
Each finalist cell passed 32/32; final diagnostic quality passed 12/12 per arm.
The exact baseline is restored. Independent review recommends bounded APC
promotion after human approval; no production promotion has occurred.

## X / short post

GLM APC on two RTX PRO 6000 Max-Q GPUs: shared-prefix visible latency 23.7→7.6s,
3.17× completed requests/s; fresh-prefix throughput −2.39%. Matched 32-request C4
cells, about 25K actual prompt tokens, strict output/canaries. Synthetic workload;
not a coding-intelligence gain. Baseline restored; promotion still human-gated.

## Reddit

We tested the same pinned GLM-5.3-Flash EXL3/v84 configuration with APC off/on.
The warm shared-prefix population improved substantially while the fresh-prefix
population showed a small cost. All four 32-request finalist cells passed exact
output and canary checks, and isolated counters measured 75.58% cache reuse.
Quality, images and long-context diagnostics passed. Reasoning volume differed,
so request throughput and visible latency lead the report. This does not prove
SWE quality or four simultaneous maximum-context sessions.

## Screenshot alt text

Matched baseline and APC latency charts show a strong shared-prefix benefit
and a small unique-prefix cost. Each cell contains 32 requests at C4.

## Claim ledger

| Claim | Conditions | Evidence |
|---|---|---|
| Shared TTFT −67.84%; completed requests 3.17× | Warm shared cache, 32 requests each, C4 | [Derived comparison](matched-comparison.json) and native source hashes |
| Unique request throughput −2.39% | Separate fresh-prefix 32-request cells | [Derived comparison](matched-comparison.json) |
| Cache reuse 75.58% | Isolated before/after counter interval | [Cache delta](candidate-shared32k-cache-delta.json) |
| Final quality 12/12 each | Three repetitions, bounded diagnostics | [Baseline](baseline-quality-final.json), [candidate](candidate-quality-final.json) |
| Baseline restored; no promotion | Exact identity and direct/routed acceptance | [Restoration](restoration.json), [review](independent-review.md) |
