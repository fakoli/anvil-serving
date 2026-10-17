# GLM serving comparison evidence — 2026-10-17

The [finding](../2026-10-17-glm53-serving-performance.md) explains the complete
matched natural-output/capacity-edge scope and the failed controlled-output scope.
This is a local documentation artifact set, not a model promotion or fleet release.

- [All timing and resource tables](analysis/final-closed/comparison-tables.md)
- [Exact numeric comparison](analysis/final-closed/summary-table.json)
- [Seven-metric chart](benchmark-matrix.svg)
- [Decision and exact coverage](summary.json)
- [Native comparison envelope](analysis/final-serving-comparison-evidence.json)
- [Frozen comparison plan](plans/comparison-plan.json) and [request run plan](plans/run-plan.json)
- [Independent measurement review](plans/final-measurement-independent-review.json)
- [Paired controlled failure](plans/paired-controlled-diagnostic-adjudication.json)
- [Original timeout adjudication](plans/original-controlled-stop-adjudication.json)
- [Collection recovery](plans/collection-recovery.json)
- [Final restoration](restoration/final-restoration-receipt.json) and [independent review](plans/final-restoration-independent-review.json)
- [Source/redaction provenance](private-origin-provenance.json)
- [Closed artifact inventory](artifact-manifest.json)

All 24 accepted cells retain the 17 required metrics plus four supplemental
families, native responses, per-request timings and in-flight telemetry. They
contain 12 matched pairs and 84 scored requests per arm. Six warmups, two failed
one-request diagnostics, the original controlled timeout and excluded attempts
are retained separately. The frozen 42-cell plan remains closed partial:
24 accepted, one failed, 17 blocked, zero pending. No missing value is imputed.

The public bundle sanitizes operator identities, paths, endpoint ports and GPU
identifiers, while retaining numerical measurements, request populations and
model/image revisions. Structured references bind the sanitized public bytes;
private-origin-provenance.json preserves the source-to-public hash map.
Compressed telemetry is deterministic gzip. The original timeout's large stream
uses ordered whole-record chunks and a concatenated sanitized-stream hash.

Eleven historical JSON snapshots are explicitly archived as .json.txt with
original-source hashes. They preserve superseded evidence and are not current
required-reference or completeness proof. Their old bindings may be stale.
The current evidence graph is separately validated. Frozen source and collector
snapshots document the actual measurement producer; later source safeguards
were applied only after final restoration. Sanitized source snapshots are
documentary evidence, not executable installations: redaction can alter syntax.
The frozen source revision and original file hashes identify the executing code;
the corresponding public product source is available at that Git revision.

The authoritative model repositories and exact revisions are pinned in the
comparison plan: [NVIDIA FP4](https://huggingface.co/nvidia/GLM-5.3-Flash-FP4-Local)
and [EXL3](https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw).
External recipe guidance is not local performance proof. The earlier quality
campaign remains a [separate artifact set](../2026-10-17-glm53-nvidia-fp4-ab-evidence/README.md).
