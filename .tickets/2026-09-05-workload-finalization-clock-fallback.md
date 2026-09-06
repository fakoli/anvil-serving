# Preserve authoritative decisions when workload observation time fails

Status: implemented candidate adf3103a; consolidated acceptance pending.

During workload-visibility:T009 implementation, a regression exposed that a
clock failure after a pending terminal proposal could drop the existing
DecisionLog record. Observation failure must not suppress authoritative
decision history. Finalization now appends the legacy decision without optional
workload metadata when its observation clock is unavailable, while clearing
active and finalizing counters.

The focused registry/decision/hardening gate passed 107 tests. No new terminal
store, raw error field, live state change or acceptance claim was introduced.
