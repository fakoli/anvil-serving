# Flare inference diagnostics evidence

This is selected sanitized evidence for a closed diagnostic wave, not complete serving qualification. The private record retains full host samplers, source snapshots and operational receipts.

- [Summary](summary.json), [artifact manifest](artifact-manifest.json), [provenance](provenance.json), [restoration](restoration.json), [source registry](source-registry.json).
- [Plan](plan/execution-plan.md), [interpretation](plan/analysis-contract-v2.md), [coverage](plan/coverage-and-gaps.md), [friction](plan/friction-log.md), [next experiments](plan/next-experiments.md).
- [Chart](graphs/matrix.svg), [chart data](graphs/graph-data.json), [graph specification](graph-manifest.json).

All 32 capacity artifacts and their request journals are retained, including priming and failures. `.gz` files use deterministic gzip; decompress before feeding them to the unchanged offline analyzers. Keep their original basenames together. The provenance table maps original source hashes to sanitized public payloads; an original analysis hash is not a hash of redacted bytes.

Recipes are sanitized reconstruction records, not executable operator configuration. Use the private registry and supported Anvil load/status/logs/unload commands. The existing kernel-tuning skill supplies trace admission; native fleet release remains separate. No new plugin, promotion or upstream submission occurred.
