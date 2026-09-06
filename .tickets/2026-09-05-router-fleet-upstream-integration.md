# Router fleet integration with request diagnostics

Status: closed for this merge; later changes and deployment remain gated.

## Reason for change

Concurrent upstream PR #470 (26fffff7) adds trusted request correlation,
streaming measurements, bounded diagnostics, and authenticated readiness
probes. The router-fleet work adds qualified-member readiness and scoped
operator authorization. Both change shared gateway seams.

## Required resolution

- Preserve authenticated health/model probes and qualified-member identity
  redaction, strict counters, and cache-generation fencing.
- Preserve request correlation reset before all HTTP dispatch, including
  scoped operator routes, and the independently bounded operator reader.
- Keep request measurements, cancellation cleanup, legacy authorization,
  and configured operator-policy forwarding in the merged server builder.
- Verify the combined source, not just both parent commits. No live route,
  controller, model, or private configuration change is implied by this merge.

## Evidence boundary

Fleet T010 source commit 3b08566f passed 77 focused tests, Ruff, and independent
review. State accepted that source after reporting a stale/conflicting base;
that acceptance is not evidence that the merged result works. The source
proof is retained; independent compatibility review and combined regression
results must close this ticket before shipment.

## Combined-tree regression evidence

- Router suite: 636 passed in 87.46 seconds.
- Full repository suite: 5172 passed, 10 skipped in 225.66 seconds.
- Ruff over product and tests: passed.
- Staged diff whitespace check: passed.

These checks ran on the resolved merge, retaining both parents' behaviors.
Independent compatibility review passed all eight angles plus 115 focused and
94 regression tests. Same-connection inference/operator/denial probes proved
correlation reset; disabling reset reproduced stale-ID leakage. Authenticated
member mismatch and invalidation probes preserved redaction/cache fencing.
A hostile eager-resource close still released its concurrency lease.
Subsequent feature edits must run their own gates; these results do not certify
a later revision or any live deployment.
