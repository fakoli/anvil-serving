# Global Fleet JSON drops the useful report on a nonzero gate

**Status:** Resolved 2026-08-30

## Problem

`anvil-serving fleet version` correctly exits nonzero when a reachable declared
host is missing the CLI or runs a different version. Human output preserves the
per-host rows and summary, but global JSON mode captures that output and then
replaces it with a generic `execution_failed` envelope whose `data` is null.

Automation can therefore learn that the release gate failed but cannot learn
which host was skewed, missing, unreachable, or timed out. The diagnostic loss
is caused by the global dispatcher discarding successful partial data whenever
the legacy handler returns a nonzero code.

## Required behavior

1. Keep the existing nonzero gate for version skew and missing installations.
2. Return the complete typed version report from library code and let the CLI
   wrapper render it.
3. Preserve that report as `data` in the standard global JSON error envelope,
   with a stable typed error code that distinguishes a failed Fleet version
   gate from an unclassified execution failure.
4. Keep human output and redaction behavior intact; do not expose credentials,
   command payloads, or capability-bearing private endpoints.

## Acceptance

- A hermetic missing-installation case exits nonzero and its global JSON
  envelope contains every host row and all summary counts.
- The envelope uses a Fleet-specific error code and explains why the gate
  failed.
- A successful global JSON call returns the same typed report shape.
- Existing human `fleet version` output and exit semantics remain covered.

## Resolution

Fleet version collection now returns a typed report independently of CLI
rendering. A failed skew or missing-installation gate keeps its nonzero exit
and returns `fleet_version_gate_failed`, while the global JSON envelope retains
every host row, summary count, warning, and typed error. Successful JSON and
existing human output use the same report shape.

Hermetic success, missing-installation, version-skew, unreachable-host,
redaction, human-output, and global JSON cases are included in the focused
release regression, which passed 531 tests on the staged candidate.
