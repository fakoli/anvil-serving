# Publication summary: GLM-5.3-Flash ormandj v0.4.2

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Decision:** `held-for-strict-turnover`; no production promotion or router admission.
- **Matched cells:** candidate improved median TTFT/E2E/decode by -5.19%/-4.91%/+3.93% at 4K n12 and -1.31%/-1.45%/+17.21% at 120K n3.
- **Caveat:** strict turnover was 58/60, then 57/60 on one unchanged repeat; candidate routed validation was intentionally not attempted.
- **Evidence:** [finding](../2026-09-09-glm53-ormandj-v042.md) · [manifest](artifact-manifest.json).

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Candidate is comparatively faster in bounded cells | direct C1, strict eligible 4K/120K populations | [comparison](comparison.json) |
| Candidate is held | strict turnover misses; no exception | [finding](../2026-09-09-glm53-ormandj-v042.md) |
| Baseline was restored | direct/routed 2/2 baseline checks | [restoration](restoration.json) |
