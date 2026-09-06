# Bound fleet workload composition and controller read deadlines

Status: implementation contract refinement; no live changes.

The node reader is source-ready, but T013 still needs explicit composition and
runtime boundaries before fleet fan-out is safe to implement.

- FleetResult permits1000 records across all nodes; each valid NodeResult can
  already contain1000. Concatenating valid node results can therefore fail the
  aggregate constructor. Preserve every declared node's summary while trimming
  records in one deterministic global order and reconciling source omissions.
- ControllerTransport.execute verifies expected_node through GET /health and
  then POST /tools/call. Each currently receives the full timeout separately.
  A timeout_seconds=2 argument is not a two-second end-to-end deadline.
- A per-request executor whose shutdown waits for blocked calls violates the
  five-second collection bound. Repeated detached per-request executors can
  accumulate workers. Fleet collection must own at most four persistent
  workers, discard late results, and retain no unbounded request queue/history.
- The ordinary telemetry collector deliberately carries bounded diagnostic
  detail. It is not a workload serializer; addresses, raw responses and
  TransportError dictionaries must stop at the new canonical workload reader.

Implementation belongs in bounded sibling modules, reusing ControllerTransport
for authenticated expected-node calls and the canonical workload schema for
results. Do not add SSH fallback, discovery, lifecycle actions, or a second
mutable workload registry. T013.1 closes pure composition first; subsequent
transport/deadline/owner wiring will be closed before claiming those slices.
