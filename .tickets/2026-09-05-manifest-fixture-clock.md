# Freeze filesystem time alongside the manifest test clock

Status: reproduced; test-fixture repair pending.

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
Candidate repair tests and the complete source rerun remain pending, as do
consolidated acceptance and deployment.
