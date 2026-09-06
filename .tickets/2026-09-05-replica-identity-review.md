# Replica eligibility accepted unverified identity snapshots

## Status

Source repair implemented for workload-contract-repairs T016 and inspected by
the integrating reviewer. Final batch acceptance and merge are recorded in the
merge checkpoint ticket; deployment and live qualification are not included.

## Reproduction

At `ab5493a2`, `RoutingBackend` passed each member's raw `AvailabilityResult`
directly to model-agnostic admission. Health-only, observed-model mismatch, and
internally inconsistent snapshots with `available=True` dispatched a backend and
were recorded as `identity_passed` solely because admission selected a declared
member ID. Malformed, subclassed, and truthy non-boolean results were not all
rejected at the same boundary.

## Repair

One pure predicate now requires the exact `AvailabilityResult` type, literal
`available is True`, ready state, `identity_passed` reason, a nonempty tier model,
and exact expected/observed model equality. Request routing normalizes every
failure to a fixed unavailable snapshot before model-agnostic admission.
Readmission and capacity projection reuse the predicate, and decision evidence
requires an explicitly verified selection instead of inferring identity from a
member ID.

The router gate also reproduced stale replica-only readiness helpers in
`tests/router/test_streaming_relay.py` and `tests/router/test_backends.py`.
Those helpers now supply the fixture tier's exact expected and observed model;
direct-tier and generic admission fixtures are unchanged.
The real server capacity-strategy fixture in `tests/router/test_model_routes.py`
also now passes its distinct configured model to that shared readiness helper.
The replica terminal-decision provider in
`tests/router/test_observability_hardening.py` now reports exact tier identity
for its ready branch and a redacted mismatch for its unavailable branch.

No direct-tier behavior, lifecycle authority, endpoint configuration, or live
operator state changes in this repair.

Postcommit proof at `5fc7a986` passed 1164 router/lifecycle tests, Ruff and diff
checks; State evidence is `EVA2FEF71F`. The health-only, mismatched, inconsistent,
subclass, non-boolean and malformed readiness cases make zero backend calls.
Replacing the eligibility predicate with an always-true function makes the
health-only dispatch assertion fail. The independent root inspected the
production/test diff before integration; no per-task deployment was attempted.
