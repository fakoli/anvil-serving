# GLM coding diagnostic evidence

Six targeted jobs completed: one pass and five failures. Two supported repetitions were unstarted; four requested lower-reasoning cases were blocked. This is diagnostic evidence, not a model-wide score or engine ranking.

- [Summary](summary.json): authoritative repeated counts, captured token usage, time and sampled memory.
- [Native campaign report](campaign-report.json): accepted populations and all eight supported cells.
- [Case diagnostics](case-diagnostics.json): native grades, per-response usage and compiler signals.
- [Requested coverage](coverage.json): all twelve requested cells, including blocked/unstarted work.
- [Rust repair excerpts](rust-repair-excerpts.json): bounded generated-code examples and their provenance.
- [Replayed historical R12 report](r12-replayed-report.json): retained cancelled grades stay outside accepted populations.
- [Identity controls](identity-control-map.json), [source registry](source-registry.json), [validation](validation.json).
- [Budget stop](budget-stop.json), [restoration](restoration.json), [friction log](friction-log.md).
- [Projection ledger](projection-ledger.json), [projection validation](projection-validation.json), [supplemental provenance](supplemental-projection-ledger.json).
- [Artifact manifest](artifact-manifest.json), [manifest source](artifact-manifest-source.json), [size exception](size-exception.json).

The JSONL files under `projections/` retain campaign controls, prepared child plans, terminal job evidence, Aider observations and telemetry. The projection ledger maps every native source to its exact public slices. Six raw chat histories remain private; the public bundle includes metadata and reviewed code excerpts, not full prompts or reasoning text. Original-private hashes and public derivative hashes have different meanings. Sanitized bytes cannot revalidate the original native identity chain.

Twelve response observations have server-reported usage; three further request starts lack responses, so all-request token totals remain unknown. A quality measurement can be complete while token accounting remains incomplete. No promotion, lifecycle change or Ignis run occurred.
