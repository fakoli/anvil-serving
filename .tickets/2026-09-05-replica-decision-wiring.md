# Carry one replica selection through all terminal decision paths

Status: implemented candidate; qualified-replica-sets:T011 pending consolidated acceptance.

Source preflight found selected_member only in RoutingBackend.generate's local
scope. The original task listed decision_log.py but omitted serve.py, so a
schema-only change could not record the actual selected lease. The existing
_record helper also creates an attempt for pre-selection refusals. Close the
task contract before implementation: two optional allowlisted decision fields,
zero attempts before replica selection, one afterward, fixed replica attempt
reasons, all terminal callbacks wired, and explicit safe summary/line/JSONL
projections. Preserve direct-tier compatibility and do not add a readiness
probe, per-member map, retry or new lifecycle authority.

Candidate 10b49e4c wires the lease member through eager, streaming, completion
and cancellation paths, uses the closed optional metadata projection on every
audit surface, and explicitly allowlists durable record fields. Its claim-bound
focused gate passed 46 tests and Ruff. Final acceptance and deployment remain
open with the overall batch.
