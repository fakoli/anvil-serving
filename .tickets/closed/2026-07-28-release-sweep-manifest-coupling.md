# Release-sweep manifest coupling

## Problem

`tests/test_release_sweep_evidence.py` compared a historical July 27 release
sweep directly with the current generated CLI manifest. Adding or removing any
valid command therefore failed the suite unless the historical evidence was
rewritten to claim coverage it never recorded.

## Decision

- Keep the dated evidence immutable and bound to its recorded repository
  revision and manifest digest.
- Pin its exact historical revision, manifest digest, command counts, and
  operation classes, then validate their internal accounting.
- Keep current-manifest coverage in the deterministic command-manifest and CLI
  audit tests, which regenerate and validate the live command surface.

## Verification

- Historical release evidence remains unchanged.
- The focused release-sweep test passes after adding new CLI commands.
- The command manifest and full CLI audit independently validate the current
  surface.
