# Freeze filesystem time alongside the manifest test clock

Status: source candidate repaired; complete source rerun pending.

Full source verification at f0a369b2 produced 33 failures in
tests/test_manifest_workloads.py after real UTC time passed its frozen
2026-09-05 23:00 test clock. The helper writes real files without fixing their
mtime. The production observer correctly quarantines them as future-dated,
preventing later runtime assertions from exercising their intended paths.
The run finished with 6974 passed, 33 failed and 14 skipped; it is not a
passing source gate. A focused repeat of the first case reproduced the same
future-workload-timestamp result.

Pin test-only file mtimes, including regular siblings, relative to the injected
clock. Retain explicit future-file overrides and add the exact 30/31-second
boundary with a valid peer. Do not change production timestamp guards, replace
the fixed clock with wall-clock time, or move the fixed date farther ahead.
The candidate pins primary and regular sibling fixtures to 22:00 UTC, while
intentional future timestamps remain explicit overrides. All 139 focused
manifest tests and Ruff pass. The new real-file 30/31-second boundary test
rejects a deliberately weakened 31-second guard in an isolated process.
Production code is unchanged. The complete source rerun, consolidated
acceptance and deployment remain pending.
