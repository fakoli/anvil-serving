# Recipe restoration needs retained source provenance

## Observation

During ThinkingCap preparation, managed recipe status identified an existing
candidate by registry and semantic digests but did not identify its source
registry. The exact registry was later found in a separate private qualification
directory and both hashes matched. Another active campaign then replaced the
candidate, invalidating that starting-state snapshot.

The installed 1.2.1 launcher also lacked the memory-limit renderer used for that
candidate. Its recipe requires a 52 GiB RAM ceiling and 1 MiB swap allowance,
and its startup command checks those limits. Rendering with a launcher that
silently omits them cannot restore the running workload.

Bounded read-only container inspection was used to diagnose command, mounts,
containment, and non-secret environment differences. No container was stopped
or replaced by the ThinkingCap campaign.

## Required closure

- Retain protected registry provenance and the launching implementation identity
  alongside managed lifecycle evidence; do not put operator paths in public
  container labels or public artifacts.
- Make a restoration preview fail closed on unsupported resource-control fields
  and on changed container identity.
- Add independent tests for lost source provenance, launcher incompatibility,
  and concurrent ownership changes.
- Coordinate with the active campaign before changing its serving installation.

## Status

Open. ThinkingCap serving is deferred until the other campaign releases the
GPU lane and a fresh, compatible restoration preview passes. The model cache,
runtime cache, and candidate recipe preparation are complete.
