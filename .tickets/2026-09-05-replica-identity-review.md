# Replica eligibility accepted unverified identity snapshots

## Status

Source repair candidate implemented for workload-contract-repairs T016. Independent
acceptance, merge, deployment, and live qualification remain pending.

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
