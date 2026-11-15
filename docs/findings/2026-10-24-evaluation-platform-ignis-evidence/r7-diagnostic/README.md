# R7 cancelled pilot: diagnostic evidence packet

Full native projection was refused because child evidence references no longer matched the retained target bytes. This packet preserves that failure; it does not replace the missing complete native chain or make invalid intervals valid.

- [Native terminal report](campaign-report.json): all 96 rows, unchanged JSON values including invalid/unverified dispositions, with only the CLI envelope removed and deterministic serialization applied.
- [Reference audit](reference-audit.json): all 20 submitted artifact roots, expected and observed SHA-256/byte lengths kept separately. The later audit is scoped to references reachable from each artifact; it does not export native payloads or validate their semantics.
- [Summary and terminal command outcomes](summary.json), [source bindings](source-registry.json), [failure limits](friction-log.md), and [retained incumbent observations](restoration.json).
- [Artifact manifest](artifact-manifest.json) inventories this diagnostic packet only. [Publication summary](publication-summary.md) gives its bounded interpretation.

The campaign stopped at 20 submitted of 96 planned cells: 16 completed, one failed, three cancelled, and 76 unrun. Seventeen known graded outcomes comprise 15 passes and two failures; one passing task failed measurement closure. These are not 17 complete measurements or a comparative quality estimate. The candidate was never loaded and the original incumbent was never unloaded.

Private originals remain authoritative. The complete frozen workload, prepared plans, raw child payloads and four-plane telemetry are deliberately absent here because full projection failed. Their absence is a documented publication gap, not permission to rank models using this smaller packet. Wrappers were assembled after abort. Independent review and whole-experiment size admission remain pending; this directory is outside Git.
