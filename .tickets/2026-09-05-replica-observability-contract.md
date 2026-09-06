# Distinguish replica readiness, admission and declared provenance

Status: source correction complete; coordinator evidence gate pending
Priority: P1
Date: 2026-09-05
Task: qualified-replica-sets:T005

## Reason for change

Existing model projections assume one endpoint per logical tier. Applying them
to the new closed replica shape would probe the empty logical URL sentinel,
show the tier as unavailable despite ready members, or mistake a single
endpoint's configured KV estimates for aggregate replica capacity.

## Implementation contract

- Keep one alias and logical tier; expose at most 16 ordered member IDs,
  qualification references, fixed readiness codes and exact-match served identity.
- Mark deployment provenance as declared and runtime deployment identity as
  unverified. A qualification reference does not prove evidence passed.
- Read aggregate/member in-flight counts from one atomic admission-owner snapshot.
  An absent or inconsistent owner is unavailable, not idle. The projection API
  accepts the owner explicitly; HTTP owner wiring is separately required by
  T005.2 and must pass before coordinator gate T005 completes.
- Preserve shared per-request limits. Do not fetch metrics using the logical URL
  sentinel, multiply KV estimates, or claim qualified aggregate throughput.
  Replica live metrics explicitly remain unavailable/not aggregated in this slice.
- Exclude endpoints, topology identities, auth material, unexpected observed models,
  free-text admission reasons, arbitrary readiness strings and raw exceptions.
- Include safe membership/provenance changes in the public config fingerprint,
  without hashing private endpoint values into that public projection.

## Verification so far

Focused metadata/capacity tests: 33 passed. Existing observability hardening:
5 passed. Ruff and diff checks passed. A negative control that restored an
unexpected upstream model to a member projection failed the identity/redaction
assertion as expected. Independent review, post-commit State proofs, publication
and runtime/deployment acceptance are not yet complete.

Independent review reproduced three additional contradictions: an ineligible
member could retain the success reason `identity_passed`; a forged owner could
claim admitting and draining simultaneously; and an unbounded owner counter
could pass reconciliation but fail JSON serialization. The correction maps
inconsistent success to unavailable, requires draining to imply quiesced, and
bounds all counts to the exact JSON-consumer integer range `0..2^53-1`.
Boundary/overflow and contradictory-state regressions are required before
acceptance. The pre-correction full suite passed 4,860 tests with 10 skipped;
this does not supersede those independently reproduced failures.

Corrective review verified those three fixes, then reproduced the unresolved
caller integration: `RoutingBackend.model_capacity()` does not pass its owner,
so an actual endpoint still reports admission unavailable even with an active
replica lease. The original task partition omitted that caller from T005 and
did not bind its later wiring to an acceptance test. T005 is not accepted while
that gap remains. Expand the scoped task to include the caller and a real HTTP
endpoint regression after the overlapping scoped-auth task releases its claim;
do not call the helper-only tests end-to-end capacity proof.

The approved amendment splits pure projection into T005.1, actual HTTP wiring
into T005.2, and retains T005 as a coordinator integration/evidence gate with
explicit dependencies. Dotted task IDs are not assumed to create State parent
links. Reviewed checkpoint a5a52258 preserves the pure implementation.

## Corrected source and combined verification

T005.1 was accepted at `d084f5a22f8e1d27bd83582cc11d6ea9018e87bf`
with State proof `qualified-replica-sets-T005.1-E000456.json`. T005.2 was
accepted at `f79b7cfc49664c2fcc5cd5b314e079a2a4bb7648` with proof
`qualified-replica-sets-T005.2-E000460.json`. The actual HTTP path now uses
the exact injected admission owner, including a falsey owner; default and
durable construction initialize the configured member map. Unauthorized HTTP
does not read the owner. Absent or forged owner state remains unavailable.

The combined source revision is
`6518f5195a6934c26d958b565b985af8c1e56288`. Independent Terra review of the
HTTP wiring passed 61 capacity/admission/transition tests. Coordinator
verification at the combined revision passed:

- `python scripts/run_tests.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py -x -q` — 43 passed.
- `python scripts/run_tests.py tests/router/test_observability_hardening.py -x -q` — 5 passed.
- `python -m ruff check anvil_serving/router/model_metadata.py anvil_serving/router/model_capacity.py anvil_serving/router/serve.py tests/router/test_model_metadata.py tests/router/test_model_capacity.py` — passed.

The coordinator task modifies only this ticket and reruns these exact gates
after commit for its claim-bound proof. Earlier failures above are retained as
history, not current unresolved source findings. This closes the projection
and HTTP-owner gap only: member dispatch, qualification, live identity,
publication, and deployment remain separate feature/release gates. No runtime
acceptance is inferred from these source tests.
