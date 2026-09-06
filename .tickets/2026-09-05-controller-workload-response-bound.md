# Explicit controller workload response bound

Date: 2026-09-05
Status: source candidate integrated; consolidated acceptance and deployment pending
Task: workload-visibility:T006.1.2

## Reproduction

The canonical fleet reader configures `ControllerTransport` with
`MAX_JSON_BYTES` (8388608). Construction currently raises
`ValueError: max_response_bytes must be between 1 and 1048576`, before the
first HTTP request. The existing node reader tests pass before this new fleet
case fails. A fake transport accepts the configuration; the real shared
validator is the blocking seam.

## Change

Allow explicitly configured controller response bounds through 8 MiB in its
constructor, execute override and status override. Preserve the 64 KiB default
and 1 MiB Local/SSH upper bounds. Keep limit-plus-one reads and existing
overflow/error semantics. Do not bypass ControllerTransport with a bespoke
HTTP client, broaden caller defaults or change identity/authentication policy.

## Evidence required

Exact-limit and one-byte-over cases, invalid typed bounds, constructor and
per-call overrides, default and unrelated-transport cap regressions; canonical
workload bound agreement. Record candidate commit and passing command evidence
before resuming T006.1. Final acceptance remains part of the consolidated batch.

## Candidate evidence

Commit `759659fa` implements only the controller cap and regression tests.
The new exact-bound test first failed on the unchanged 1 MiB constructor cap.
Postcommit transport/node-reader gate: 123 passed; scoped Ruff passed.
Claim-bound evidence: `EVE1309262`. T006.1 resumed with this prerequisite
integrated. These are local source results, not deployment or final acceptance.
