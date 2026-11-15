# Flare GLM trial evidence — 2026-10-21

Bounded scout complete; full serving comparison incomplete; `no-promotion`.
Measured on two RTX PRO6000 Blackwell Max-Q GPUs under native Linux. Product
base80ad7e5c plus retained uncommitted sampling/recipe fixes. All raw artifacts
keep their native schemas; sanitized copies replace operator paths, host/network
identities, topology runtime/resource/controller names, container IDs and GPU UUIDs. Numeric measurement values are unchanged. Private
originals, signing key, local image and volumes remain with their owner.

- [Decision and authoritative metric table](summary.json)
- [Dated finding](../2026-10-21-flare-glm53-trial.md)
- [Exact file hashes and roles](artifact-manifest.json)
- [Dated external sources](source-registry.json)
- [Pre-request scout plan](raw/scout-plan.json) and [source verification](raw/benchmark-source-verification.json)
- [Pinned reconstruction](configuration/README.md)
- [Coverage and gaps](coverage-and-gaps.md), [friction](friction-log.md), [public summary](publication-summary.md)
- [Verified restoration](restoration.json) and [closure receipt](raw/restoration-final.json)
- [Machine-derived charts](graphs/matrix.svg), [chart data and input hashes](graphs/graph-data.json), [graph manifest](graph-manifest.json)

Native `anvil-serving.benchmark/v1` files under `raw/` retain all eight natural
cells, four failed strict cells, distributions and request journals. Preflight,
startup, package, resource, and restoration receipts preserve their separate
scope. C4 cells are warm from preceding C1, despite unique prefixes within each
run. Eight requests per natural cell do not establish stable p99. Model and
runtime pins differ between arms; there is no causal engine speedup claim.

The runtime sample journal is losslessly gzip-compressed. Original SHA256: 8486575f1b1f1bcff88cbb6170a7d24defd499e0a76ef1bb35938e74ff1a17a9. Published gzip SHA256: 13040d1e24c1806803858bcb0a474833d338279d611edbd76b1a0ef8fa75967a. All original lines and bytes are preserved on decompression.
