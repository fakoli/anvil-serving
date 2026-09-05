# Optional metadata-only trace export

Status: open
Priority: P2
Depends on: gateway request correlation and terminal measurements

## User outcome

Join gateway request timing with a selected upstream's trace in an operator
observability tool without turning request contents into default telemetry.

## Plan

1. Define a versioned mapping from measured gateway fields to current
   OpenTelemetry GenAI/HTTP conventions; label experimental conventions.
2. Validate W3C trace context, generate a fresh span ID, and define trust and
   sampling boundaries. Do not propagate arbitrary baggage or tracestate.
3. Keep export disabled by default and separate from the stdlib request path.
   Use an explicit private destination, bounded queue and batches, drop counters,
   total send deadlines, and no retry/availability dependency for inference.
4. Export only allowlisted route/member, timing, outcome and count metadata.
   No prompts, completions, tool arguments/results, credentials or arbitrary
   headers. Recording content would require a separate policy and design.
5. Capture request-time build/config identity directly; never relabel a later
   status snapshot as historical identity.

## Acceptance

Malformed headers, fake sampling flags, unreachable collectors, queue overflow,
shutdown, adversarial secret fields and process restart must not disclose
content, block inference, mutate route choice, or manufacture a complete trace.
Independently validate a synthetic exported trace in a pinned collector. A
header alone is not evidence that the upstream or collector accepted it.
