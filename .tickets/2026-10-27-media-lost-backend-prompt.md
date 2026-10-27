# Reconcile prompts lost across a backend restart

An owned running media job could remain running forever after its backend lost
the prompt. Empty history and an empty queue were normalized to queued; a
running durable job correctly refused to move backward, so it never terminated.

The adapter now distinguishes an absent prompt from a queued one. Reconciliation
requires 30 seconds of observed absence on successful backend reads and a final
recheck before marking the job failed with `backend_prompt_lost`. An unavailable
backend resets the observation window. A completion racing either history
probe remains eligible for output capture. No render is resubmitted or interrupted.

This failure means outputs cannot be recovered from that backend, not proof
that every remote process has exited. Native drain readback retains that
uncertainty. A completion after the final read remains a remote observation
race; backend observation and local state cannot form an atomic transaction.

Validation: focused adapter/reconciler tests cover persistent loss, transient
absence, outages, cancellation-compatible transitions and completion races.
