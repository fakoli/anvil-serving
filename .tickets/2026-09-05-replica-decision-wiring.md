# Carry one replica selection through all terminal decision paths

Status: open; qualified-replica-sets:T011.

Source preflight found selected_member only in RoutingBackend.generate's local
scope. The original task listed decision_log.py but omitted serve.py, so a
schema-only change could not record the actual selected lease. The existing
_record helper also creates an attempt for pre-selection refusals. Close the
task contract before implementation: two optional allowlisted decision fields,
zero attempts before replica selection, one afterward, fixed replica attempt
reasons, all terminal callbacks wired, and explicit safe summary/line/JSONL
projections. Preserve direct-tier compatibility and do not add a readiness
probe, per-member map, retry or new lifecycle authority.
