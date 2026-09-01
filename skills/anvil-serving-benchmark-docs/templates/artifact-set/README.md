# CAMPAIGN benchmark evidence

This directory contains the sanitized evidence bundle for the dated finding.
The native benchmark artifacts remain authoritative; the files below provide a
consistent campaign-level index.

## Campaign boundary

- **Campaign ID:** `YYYY-MM-DD-SLUG`
- **Capability:** TEXT, MULTIMODAL, VOICE, STT, MEDIA, OR KERNEL
- **Repository revision:** 40-CHARACTER COMMIT plus dirty-state note
- **Evidence labels:** COMPATIBILITY-ONLY, FUNCTIONAL, CAPACITY, OR QUALITY
- **Decision labels:** CURRENT, ROLLBACK, CHALLENGER, NO-PROMOTION, OR REJECTED
- **Promotion boundary:** benchmark evidence does not authorize promotion

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - role ledger, native
  schemas, file hashes, and explicit gaps
- [`source-registry.json`](source-registry.json) - dated source provenance and
  decision impact
- [`summary.json`](summary.json) - bounded machine-readable outcome and
  decision
- [`friction-log.md`](friction-log.md) - failures, workarounds, ambiguity, and
  recurring manual steps
- [`restoration.json`](restoration.json) - starting/ending state and post-run
  verification, or the reason restoration was not applicable

## Workload and plan

Link the retained native corpus or workload manifest, benchmark plan,
configuration snapshot, and exact recipe. State selection rules, hashes,
licenses, controls, request order, repetitions, context, concurrency, budgets,
and gates.

## Raw run evidence

Link every native successful, partial, and failed artifact. Name its schema,
completion state, measurement path, warm/cold state, and what it proves.

## Decision and publication

Link the dated finding and, when required, the companion
`publication-summary.md`. State the most important
limitation and every material evidence gap.
