# Close node workload composition before adding collection endpoints

Status: bounded pure composition planned as T012.3; runtime composition pending.

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
