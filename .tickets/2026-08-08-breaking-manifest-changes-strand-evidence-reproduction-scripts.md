# Breaking manifest schema changes silently strand evidence-reproduction scripts

**Status:** Open — decision recorded, no code change proposed yet

## Problem

`docs/findings/` holds durable evidence (ADR-0027), and some findings ship a
reproduction script alongside their data. `2026-07-13-t015-resident-set-evidence/`
contains `run_eviction_cycle.py`, which loads `eviction-sim-manifest.toml`
through the product's own manifest loader.

Two such manifests exist, carrying eight `[[serve]]` entries between them:

- `docs/findings/2026-07-13-t013-vision-evidence/eviction-sim-manifest.toml` (6)
- `docs/findings/2026-07-13-t015-resident-set-evidence/eviction-sim-manifest.toml` (2)

Adding a required `runtime` field to `[[serve]]` (ADR-0034 §7) makes both
manifests unloadable, so `run_eviction_cycle.py` can no longer run.

This creates a real tension:

- Migrating the manifests would **rewrite recorded evidence**, which ADR-0027
  forbids. The manifest is part of what the finding says was executed.
- Leaving them means an evidence artifact advertises a reproduction path that
  fails on contact with current code.

Nothing in CI catches this. The manifests are not loaded by any test, so the
suite stays green while the artifact quietly rots.

## Decision taken for ADR-0034

Leave the evidence manifests unmodified. A finding records the state of the
world at its date, including the schema in force then; rewriting it to satisfy a
later schema would make the record lie about what was run.

## Required behavior

1. A finding that ships an executable reproduction script should state the
   product version or schema era it was written against, so a reader knows
   whether "it does not run today" means rot or means a defect.
2. Consider a docs gate that detects `[[serve]]`-bearing manifests under
   `docs/findings/` and asserts they are *excluded* from schema migrations —
   turning "we chose not to migrate these" into a checked invariant rather than
   tribal knowledge that the next migration silently violates.
3. More generally: any future required-field addition to a manifest schema
   should enumerate evidence artifacts it strands, as a named migration step
   rather than an afterthought.

## Notes

Found while migrating 184 `[[serve]]` entries (103 public, 81 private operator)
for the `runtime` discriminator. The evidence manifests were the only ones
deliberately skipped.
