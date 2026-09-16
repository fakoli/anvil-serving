# 2026-09-15 Anvil Media bringup evidence

This bundle retains one managed image smoke and three video diagnostics on an RTX 5090. It records functional results and sampled independent quality reviews, with no workflow promotion or performance comparison.

## Evidence index

- [Image status](image-job.json), [idempotent replay](image-replay.json), and [ownership refusal](artifact-owner-refusal.json).
- [Wan v1 native result](video-qualification.json) and [image/v1 independent review](independent-review-v1.json).
- [Wan v2 small native result](video-v2-qualification.json) and [review](independent-review-v2.json).
- [Wan v2 standard native result](video-v2-standard-qualification.json) and [review](independent-review-v2-standard.json).
- [Workload inputs](workload.json), [official sources](source-registry.json), [campaign state](campaign-state.json), and [coverage](coverage-and-gaps.md).
- [Sanitized health summary](final-status.json), [sanitized restoration summary](restoration.json), and [friction](friction-log.md).
- [Decision summary](summary.json), [publication summary](publication-summary.md), and [artifact manifest](artifact-manifest.json).

The raw native receipts and real operational state are retained in the private
operator repository. Public qualification and image records preserve the model
identity, measurement values, and timestamps, with opaque job/artifact identifiers
replaced by consistent synthetic values. Public artifact resource references are
non-live. Captured endpoint/principal arguments are generic placeholders; managed
status and restoration are projections with topology withheld. See the
[redaction provenance](redaction-provenance.json).

## Boundaries

The CLI imported this checkout at revision `2b99e335` with 13 pre-existing dirty files. The image used the fixed high 1024x1024/four-step profile. Image status delay is not engine latency. Each video preset has one sample; frame reviews do not establish temporal quality.

Wan v1 remains unavailable with `quality_failed`. Corrected v2 remains unavailable with `quality_unverified` after partial reviews. Saved browser workflows are `Anvil Image - High` and experimental `Anvil Video v2 - Preview`.

Post-run health passed. Live assignments and reservation details remain private.
Both three-asset model bundles passed exact hash checks without downloads.
