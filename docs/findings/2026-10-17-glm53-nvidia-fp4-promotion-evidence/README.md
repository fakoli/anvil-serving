# NVIDIA FP4 promotion evidence index

This is the sanitized documentation view of the human-approved primary
selection on 2026-10-17. The operator considers this pinned recipe the best
current fit for this deployment. Full native promotion/client captures remain
private; their summary hashes are retained in [summary.json](summary.json).

| Record | Purpose and boundary |
| --- | --- |
| [Promotion finding](../2026-10-17-glm53-nvidia-fp4-promotion.md) | Selection rationale, exact configuration, acceptance, KV interpretation, rollback, and incomplete fleet gates |
| [Promotion summary](summary.json) | Machine-readable current decision, acceptance/source hashes, client and publication status |
| [Initial promotion summary](initial-promotion-summary.json) | Immutable earlier fleet-state snapshot, superseded by supported recovery in the current summary |
| [KV accounting](kv-accounting.json) | Configured byte pool, measured C4 block fractions, estimated occupied/unused GiB, and source hashes |
| [Full serving comparison](../2026-10-17-glm53-serving-performance.md) | Authoritative comparison of the FP4 and EXL3 recipes across 12 matched pairs |
| [Comparison tables](../2026-10-17-glm53-serving-performance-evidence/analysis/final-closed/comparison-tables.md) | p50/p95 timing, prefill, decode, TPOT, throughput, tokens, memory, KV, and concurrency |
| [Comparison evidence index](../2026-10-17-glm53-serving-performance-evidence/README.md) | Original plan, native results, telemetry, failures, and historical restoration |
| [Comparison artifact manifest](../2026-10-17-glm53-serving-performance-evidence/artifact-manifest.json) | Hash-bound native evidence inventory; the original comparison's no-promotion close is historical |

The fresh promotion capacity population has 12 scored requests after one
excluded warmup; the earlier natural/edge comparison has 84 scored requests
per arm. Native client smokes are functional evidence and do not remeasure
engine timing or KV. Their unavailable metrics and excluded captures remain
explicit. No new inference ran to prepare this documentation.

The older workflow is now cancelled and verified quiescent. Corrected native
profiles passed staging, while full catalog/session acceptance and the declared
secondary dependency remain pending. The promoted primary and retained
rollback are separate verified states; documentation success does not prove
fleet acceptance or four simultaneous full-window decodes.
