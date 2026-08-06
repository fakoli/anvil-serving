# Duplicate serve names across aggregated manifests silently shadow, and stale registry paths surface only at up-time

**Status:** Open (live operator config repaired 2026-08-06; product gaps remain)

## Problem

On Fakoli Dark the serve
`tp2-deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k` was defined in
**both** `serves.toml` and `serves.tp2-campaign.toml` in the same operator
home. The manifest aggregator accepted the duplicate without a warning and the
`serves.toml` entry won. That entry carried
`--registry {dir}/../../configs/...-recipe.toml` — a relative path written
when the operator home lived inside a repository worktree
(`examples/fakoli-dark`). After the operator home moved to a standalone
directory outside any repository, `{dir}/../..` no longer lands in a
checkout, so the first `serves mode enter` of the night failed at the `up`
step with:

```text
FAILED: serve-recipe registry not found: <operator-home>/../../configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml
```

The failure appeared only after the full live-state scan and the transactional
mode entry had begun, and it triggered a split-stack restore that had its own
failure (see the rollback-image ticket of the same date).

The paired `serves.tp2-campaign.toml` definition had the correct absolute
registry path the whole time — it was silently shadowed.

## Live repair applied

Both `serves.toml` entries (650k and 1m) now point at the absolute recipe
paths under `anvil-serving-wt-deepseek-r16-maxseq16-igpu/configs/`. A dated
backup `serves.toml.anvil.bak.2026-08-06` was taken first.

## Required behavior

1. Manifest aggregation must refuse — or at minimum loudly warn on — two
   `[[serve]]` entries with the same `name` or `container` across the loaded
   manifest set, stating which file wins.
2. A serve whose `up` command references a `--registry` path should have that
   path existence-checked during plan construction (`mode enter` preview /
   `serves render` / `serves up` pre-dispatch), not discovered mid-transaction
   after competitors may already be drained.
3. `{dir}`-relative traversals that escape the operator home are a smell worth
   flagging at load time; the operator home is not guaranteed to live inside a
   repository.

## Acceptance

- Hermetic test: duplicate serve name across two manifests → explicit error or
  warning naming both files.
- Hermetic test: `mode enter --dry-run` (and `serves up` preview) fails fast on
  a missing `--registry` path before any container mutation.
- Docs note in the serves CLI reference describing manifest precedence.
