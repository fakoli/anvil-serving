# Closed-campaign raw evidence: sanitization record

This directory is a public-safe, closed qualification packet. It retains selected native evidence and the complete failed initial SWE stage. The corrected SWE rerun is terminal and retained: five attempted cases, one official unresolved grade, and four empty ungraded `LimitsExceeded` submissions. This is not a promotion record.

The copies retain source JSON schemas and failure state where public-safe. Mechanical substitutions were applied recursively, including JSON stored in command-result envelope strings: operator-specific loopback ports become `30000`; operator-home, worktree, and private research prefixes become `$OPERATOR_HOME`, `$WORKTREE`, and `$RESEARCH_EVIDENCE`; host labels become `Primary Node`; GPU identifiers and container IDs are redacted. No GPU UUIDs, private endpoint names, credentials, or operator paths are retained.

Container-internal `/models/...` paths, model checksums, public image digests, model revisions, public benchmark task identifiers, and measured status/failure fields remain when needed to identify the candidate and interpret evidence. Public files bind their own hashes. Protected-original hashes are provenance only and are mapped separately in `raw/protected-original-and-public-copy-hashes.json` and `raw/deadline-original-hash-ledger.json`.

Restoration topology exports are intentionally not included. `raw/restoration-verification.json` is a derived before/after comparison with protected-original hashes and value fingerprints; it is not a native status export. The two restoration preflight artifacts retain their native shapes.
