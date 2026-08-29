# Evidence manifest: Hermes image-quality production enablement

This bounded, sanitized packet supports the
[production-enablement finding](../2026-08-28-hermes-image-quality-production.md).
It contains no credentials, private endpoint, machine-local path, GPU UUID,
job identifier, artifact identifier, or generated binary.

| Artifact | Purpose | Boundary |
|---|---|---|
| [summary.json](summary.json) | machine-readable profiles, latency phases, artifact hashes, visual dispositions, cold lifecycle, negative controls, and fix-forward results | single c1 observation per profile; cold duration includes repair iterations |
| [publication-summary.md](publication-summary.md) | canonical short-form facts, accessible alt text, and claim ledger | derivative communication only |

The eight generated PNGs are retained in the private operator evidence packet
because they are binary working evidence. Their exact byte lengths and SHA-256
digests are recorded in `summary.json`; the public claims depend only on those
bounded metadata and the independent review dispositions stated in the finding.

The live system used the display labels Fakoli Dark, Fakoli Mid Mod, and
Fakoli Mini. No display label in this packet resolves to a reachable network
identity.
