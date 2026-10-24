# Flare cache diagnostics evidence

Closed focused diagnostics, not serving qualification or a performance ranking.

- [Summary](summary.json), [manifest](artifact-manifest.json), [source registry](source-registry.json), [restoration](restoration.json).
- [Research and plan](plan/research-and-plan.md), [coverage](coverage-and-gaps.md), [next experiments](next-experiments.md), [friction](friction.md).
- [Redaction provenance](provenance.json.gz) maps every private original hash to a sanitized payload or archive member. Original analysis hashes are not hashes of redacted bytes.

Each cache/decode tar.gz contains the entire native phase directory, including bodies, responses, events, metrics, order, summaries and failures. Extract into its original phase name; counter phases belong under counter-arm/. Ordinary .gz files use gzip. Recipes and operational commands are sanitized reconstruction records; use protected private configuration with managed Anvil commands.

The analyzer and its corruption checks are retained under harness/. It consumes the original directory layout. Publication redaction changes path/body bytes, so private hash-bound analysis is authoritative and cannot be rerun on redacted bytes without translating the provenance bindings. Response text is synthetic benchmark material, not user conversations. The common artifact ledger was consolidated at closure; prospective plans and frozen execution files are separately retained.

Full source downloads remain private; pinned official URLs allow source review. No upstream patch, promotion, kernel policy change, or fleet CLI release was performed. No public social copy was requested.
