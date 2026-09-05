# Synchronize the counting lease with scheduler evidence

Status: implemented and locally integrated; consolidated acceptance pending.

## Evidence and cause

The full integrated suite at 7d88c743 finished with 5 failures, 5917 passes and
10 skips. All failures were in tests/router/test_backends.py: three eager-error
variants, the error-metadata failure case and the selected lazy-failure case.
RoutingBackend reads the acquired lease's immutable selection, but their old
_CountingLease test double does not expose that field. Real round-robin
MemberAdmissionLease instances expose selection=None.

## Fix

Scheduler T005.1 adds the missing field to the existing counting double while
preserving the exact release and no-peer assertions. Do not make production
code silently accept a malformed lease. Real-admission terminal coverage in
T007 independently exercises both scheduling strategies.

## Verification

- `python scripts/run_tests.py tests/router/test_model_routes.py tests/router/test_backends.py -x -q`
- `python -m ruff check tests/router/test_backends.py`
- Rerun the integrated full suite after local integration.

Candidate 7c4e68ec passed 64 focused tests and Ruff after commit, recorded as
EV26493E43. The integrated tree at 30b8d4db then passed the full suite:
5953 passed, 10 skipped in 248.89 seconds. This resolves the fixture defect;
final batch acceptance and live deployment are still pending.
