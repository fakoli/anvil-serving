# Health HTTP error details and execution-state classification

Status: open; deferred at the operator's source-merge stopping point.
Priority: medium. No fix or live operation is included in this checkpoint.

The independent final closure review at `f964a81e` observed a pre-existing
adjacent behavior in `ControllerTransport._verify_node`: HTTPError still uses
the general `_http_error` helper. A synthetic HTTP 503 on `/health` produced
`controller_http_error`, `remote_failed`, and response detail containing the
literal test marker `private-health-marker`, although no tools POST occurred.
The same behavior is present before the T017 repair (`2ce5fec8^`).

This is distinct from T017's now-closed duplicate-key, overflow and exact
successful health-envelope validation. Its malformed/mismatch/connect refusals
are fixed and input-free. The generic non-2xx health error path remains open;
do not claim that every health failure has the new private error envelope.

## Next bounded repair

Read `anvil_serving/transports.py::_verify_node` and `_http_error` plus the
identity fixtures in `tests/test_transports.py`. Introduce a health-only error
boundary that consumes/closes HTTPError within the existing byte/deadline
budget, does not retain a raw body or endpoint, and reports that the subsequent
operation was not started. Preserve meaningful authentication versus service
failure classifications and leave post-dispatch operation-error semantics
unchanged. Do not alter credentials, retry, discover or contact a live host.

Prove 401/403/503, oversized/non-JSON hostile bodies, throwing cleanup and
empty-body cases with injected responses: one health GET, zero tools POST,
no cached identity, fixed bounded errors and exactly-once close. Retain the
real loopback successful-health and legacy operation/status regressions.
Review the exact error-code compatibility contract before implementation.
