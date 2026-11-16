# Runtime scout evidence

The [finding](../2026-11-01-runtime-scouts.md) records two separate private-target
experiments. The original configuration was retained after each one. This public
bundle is a sanitized numerical record, not a complete reproduction package.

- [Artifact inventory and hashes](artifact-manifest.json)
- [Sources](source-registry.json)
- [Configuration, redactions and reproduction limits](reproduction.md)
- [Decision summary](summary.json)
- [Restoration](restoration.json)
- [Failures and limitations](friction-log.md)
- [Cache-layout summary](cache-layout/summary.json), [plan excerpt](cache-layout/plan-excerpt.json),
  [chart](cache-layout/comparison.svg), [chart data](cache-layout/graph-data.json)
- [DFlash summary](dflash/summary.json), [plan excerpt](dflash/plan-excerpt.json),
  [chart](dflash/comparison.svg), [chart data](dflash/graph-data.json)

Each experiment retains twelve timed native JSON artifacts per arm, named
`baseline-before-00.json` through `-11.json`, `candidate-00.json` through `-11.json`,
and `baseline-after-00.json` through `-11.json`. Each has its request journal,
counter reconciliation and sampled telemetry. Allocation and preflight files
are separate. The manifest inventories every file. Numeric fields are preserved;
the same sanitized placeholder does not imply that distinct private arms had
identical configuration fingerprints.

The source bundles were hash-verified before copying. Protected originals were
left unchanged. Public source hashes refer to these sanitized bytes. The
publication validation recomputes pooled timing statistics from retained native
request rows and verifies allocation and restoration receipts.
