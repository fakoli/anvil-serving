# Flare GLM optimization evidence — 2026-10-21

This is a closed optimization scout, not full qualification. The incumbent was restored; no promotion occurred.

Start with [the finding](../2026-10-21-flare-glm53-optimization.md), [summary](summary.json), [coverage](coverage-and-gaps.md), [source study](diagnostics/source-findings.md), [ordered plan](diagnostics/optimization-plan.md), [recipes](configuration/README.md), and [restoration](restoration.json).

## Evidence layout

- capacity/: native request metrics and losslessly compressed request journals; failed populations remain retained.
- native-*/: native benchmark evidence envelopes and stage files, with public redaction hashes rebound.
- diagnostics/: protocols, counters, package identity, telemetry, independent reviews and failed experiments.
- fixtures/: bounded diagnostic fixtures and their offline checks.
- configuration/: versioned sanitized managed recipes.
- graphs/: data-derived plots; request policies and different checkpoints remain explicit.
- [provenance](provenance.json): private-original, sanitized-uncompressed and published hashes, compression and redactions.
- [artifact manifest](artifact-manifest.json): closed file inventory, SHA256 and bytes.

A .gz file decompresses to the sanitized_sha256 in provenance. Compression changes no observations. Native capacity journal metadata describes original execution; use provenance for published-copy identity. Private originals remain intact. Public path/GPU/network substitutions are synthetic; these recipes need operator values before execution. No credentials or signing-key content is included.

Each artifact remains below 1 MiB. The retained campaign spans many bounded arms and failures; the independent size review records the exact total and inventory. No immutable public external store is configured. Removing failed populations or counter traces would weaken reproducibility.

The common templates were added after early requests. Summary complete=false and comparison_complete=false are intentional; execution and restoration are complete. There is no full frozen serving-comparison claim.

## Size-policy exception

Independent reviewer flare_trial_review (GPT-5.6 Sol, high reasoning) conditionally approved ADR-0027-FLARE-GLM53-OPTIMIZATION-COMPLETE-EVIDENCE-2026-10-21: preserve the complete closed evidence set below9MiB aggregate and below1MiB per artifact, with exact final inventory, lossless compression provenance, and all publication checks passing. Keeping only winning cells or dropping counter/failure evidence would prevent independent reconstruction; no immutable external public store is configured. Final manifest and private review record bind the delivered set.
