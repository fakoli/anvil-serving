# Sage S2.1 Flash qualification evidence

This is the public-safe evidence bundle for the dated [Sage S2.1 Flash compatibility qualification](../2026-11-21-sage-s21-flash-qualification.md).

## Campaign boundary

- **Campaign ID:** `2026-11-21-sage-s21-flash-qualification`
- **Capability:** text/tools first; image input is a separately gated capability.
- **Repository revision:** `f7fabbf1b88b38929d2d4ca97762234dbf1fca47`; documentation worktree changes are not a runtime revision.
- **Evidence labels:** `external-prior`, `checkpoint-custody`, `functional`, `measured`.
- **Decision labels:** `challenger`, `no-promotion`.
- **Promotion boundary:** no benchmark, custody, or historical-control artifact authorizes promotion.

The pinned public checkpoint pull is verified complete (94 files, 175,723,101,149 bytes, 83 weight shards, and an index with 150,226 tensors). This proves only receipt and integrity of that pinned checkpoint, not model loading or behavior.

## Artifact roles

- [artifact manifest](artifact-manifest.json) — role ledger and explicit gaps.
- [source registry](source-registry.json) — dated public source provenance and decision impact.
- [recipe comparison](recipe-comparison.md) — exact local recipe flags plus explicitly external official, EXL3, and NVFP4 alternatives.
- [summary](summary.json) — the single authoritative machine-readable campaign result.
- [coverage and gaps](coverage-and-gaps.md) — requested outcomes that are still unmeasured.
- [friction log](friction-log.md) — the known compatibility and comparison boundaries.
- [restoration record](restoration.json) — serving and router restoration are verified; the observed pretrial monitoring gap is retained and no monitoring registration or observability rollout is claimed.
- [sanitized checkpoint custody](raw/checkpoint-pull-verification.json), [checkpoint comparison](raw/checkpoint-comparison.json), [startup identity](raw/sage-startup-identity.json), and [5/5 functional preflight](raw/sage-preflight.json).
- [sampled resources](raw/sampled-resources.json) — identifier-free resource cadence and workload binding for concurrency brackets, the excluded GLM C1 cell, the R3 warmup, and measured VRAM/power.
- [sanitized historical comparator receipt](raw/historical-comparator-receipt.json) — descriptive controls only, never a Sage result.

All raw references are sanitized: endpoints, machine and operator identifiers, local paths, credentials, and scopes are omitted. Native private records remain authoritative for their unsanitized fields.
