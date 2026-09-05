# Close node workload composition before adding collection endpoints

Status: T012.3/T012.4 source candidates integrated; router reader in progress;
runtime source binding and endpoint composition pending.

The six owner projections can each return200 records, while NodeResult and
the aggregate selector accept at most1000. Passing all six results directly
to the aggregate selector can fail with1200 otherwise-valid records. Add a
bounded pure merge with exact global ordering, owner-local failure isolation,
and reconciled omission counts before wiring collection.

Source inspection also established these runtime boundaries for remaining
T012 work. They are implementation gaps, not claims of completed features:

- Benchmark/media writable constructors are unsuitable readers; T012.1 and
  T012.2 add non-initializing owner-module read entrypoints.
- The existing server OperationStore instance is authoritative; do not
  construct another owner or run recovery on a visibility request.
- Generic controller idempotent tool dispatch persists operation records,
  even for read tools. Workload dispatch must not enter that write path.
- Generic response/chaining context can carry caller or transport content;
  the workload envelope needs a canonical-only boundary after scope checking.
- Resource declarations have endpoints but no outbound credential reference.
  Router collection needs explicit local resource/credential binding; do not
  guess a port, extract inbound policy secrets, or reuse a data-plane token.
- Managed observation may take ten seconds; fleet node timeout is two seconds.
  Node composition needs a shorter collection deadline and a persistent,
  bounded in-flight source owner so slow readers cannot accumulate threads or
  block unrelated healthy sources. No new workload state machine is needed.

Close the remaining source-binding/deadline/dispatch contract in the PRD
before claiming runtime T012. No live configuration or lifecycle was changed.

T012.4 closes the coordinator contract: one active collection, a 1.5-second
deadline, at most six persistent owner workers with one in-flight job each,
no accumulated queue or result cache, source-local failure and late-result
discard, and nonblocking close. This leaves the existing ten-second managed
capture lifecycle intact while protecting workload request latency. The later
server composition must own one collector; creating it per query is forbidden.

T012.3 candidate b729b5cfec6bc7976586945172100e9cdbe0ba0f passed70 focused
tests and Ruff, recorded as EVBD0BBD19. It is integrated locally for the
remaining implementation tasks; consolidated acceptance and deployment are
still pending.

## Fix-forward: optional progress total

A literal compatibility probe on integration1e37379a reproduced a mismatch:
canonical Progress(1) has total=None and its source is COMPLETE, but composition
returns UNAVAILABLE with invalid-workload. The exact-type guard in
workload_collection._validated_progress incorrectly requires total to be an
integer even when absent. Preserve canonical unknown totals while continuing
to reject bool/subclass/invalid numeric values and forged Progress objects.
Add known/unknown/zero-total round-trip cases and malformed-total controls.
This is a narrow projection correction, not a schema or source lifecycle change.

Correction candidate21f5345657ec41d4013fad10ea3fe1eccbc43961 reproduced the
unknown-total test failure before its guard change, then passed79 tests and
Ruff after commit (EVA438E045). Coordinator candidate
63de243d82201202192693b3d1f581f463632278 passed31 tests and Ruff
(EVFD01E028), including queued disposal and failed-worker-start recovery.
Both remain candidates for the final batch acceptance.

## Explicit source binding

T012.5 closes the authenticated router wire reader. T012.6 binds six owner
readers to explicit startup paths and the exact server-owned operation store,
without writable constructors or ambient path discovery. Router visibility
uses an explicitly selected router-workloads/workloads-v1 service resource
and a separate outbound credential environment reference. Existing generic
Resource fields suffice; do not add a second role=router resource, which would
make legacy resource_owner(router) resolution ambiguous. Controller startup
will supply the reference explicitly rather than extending topology with
an unrelated credential schema. No private configuration was changed.
