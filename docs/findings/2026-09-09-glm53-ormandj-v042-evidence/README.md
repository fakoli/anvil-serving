# GLM-5.3-Flash ormandj v0.4.2 qualification evidence

This is the sanitized evidence bundle for the dated
[v0.4.2 qualification finding](../2026-09-09-glm53-ormandj-v042.md).

The retained plan fixes the model revision, TP=2, 393,216-token/C1 contract,
adaptive MTP, P2P-enabled transport, disabled cuMem, and disabled HiCache. The
candidate is a pinned source state separate from the release tag. The artifact
set is complete; the decision remains held pending an explicit user exception.

## Retained direct artifacts

- [Matched comparison](comparison.json), [baseline](baseline/), and
  [candidate](candidate/) native JSON retain functional, thinking, high-context,
  and capacity observations.
- Strict turnover retains baseline 53/60 and candidate 58/60 and unchanged repeat 57/60; no failed
  population is silently converted into a pass.
- [Workload manifest](workload-manifest.json), [restoration](restoration.json),
  and [decision summary](summary.json) record the remaining boundaries.

Public evidence redacts private addresses, DNS names, GPU UUIDs, and local
paths while retaining native schemas and a redaction record. Known baseline
needle and turnover failures, candidate startup warnings, and any final gate
failure remains visible.

`repository/` identifies files at the recorded qualification commit. `private-campaign/` and `private-only:` identify retained private inputs or logs, not downloadable public paths. Unique path names and original hashes are preserved in the workload and redaction manifests. Full operator files remain private; the restoration hash receipt records their verified equality.
