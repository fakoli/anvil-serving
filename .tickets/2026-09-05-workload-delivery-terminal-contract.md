# Workload delivery terminal contract

## Status

Open implementation gate. The workload-visibility PRD now closes ownership and delivery semantics, but no runtime workload registry or projection is shipped by this documentation change.

## Reproduced gap

On 2026-09-05, a synthetic in-memory review of the merged router lifecycle showed that `RoutingBackend.generate` appends its success decision when the backend iterator is exhausted. That happens before `RouterHandler` writes and flushes the buffered or streaming response.

The reproduction used only injected local fakes: after `generate` and the first yielded delta, the decision log was empty; advancing the iterator to completion appended one success record before any front-door delivery occurred. A later socket-write or final-flush failure therefore could not change the workload result from success to disconnected. The planned lifecycle task also omitted `serve.py`, even though that module owns configured-alias resolution, readiness, admission, dispatch, and iterator boundaries.

## Adopted correction

- `anvil_serving/router/workloads.py` will be the sole owner of `RouterWorkloadRegistry`, active phase state, pending terminal proposals, delivery-aware finalization, saturation accounting, and active/recent deduplication.
- `build_server` will construct one registry over the same `DecisionLog` used by `RoutingBackend`. Authenticated requests use `generate_tracked`; existing direct `generate` callers remain compatible and untracked.
- The registry activates only after authentication, bounded parsing, and configured-alias resolution, then observes `checking`, `admitted`, `dispatched`, and first-iteration `streaming` at their actual owners.
- Backend completion proposes a safe result. The front door commits it only after response bytes, stream terminator, and final flush; disconnect may override the workload outcome without rewriting legacy decision fields. Finalization is idempotent and always removes active state.
- `DecisionLog` remains the only terminal collection. Projection scans at most 512 recent decisions, active state is capped at 1024, and all observation failures remain unable to affect routing or admission.
- Workload identity reuses the internally generated gateway request ID. Caller correlation values and payload-derived values are excluded. The later endpoint supplies a trusted configured host to canonical ID construction rather than inferring host identity locally.

## Acceptance gate

Implementation remains open until focused tests prove the real `build_server` wiring and every phase/terminal boundary, including blocked readiness, acquired admission, eager backend failure, empty/tool-only streaming, malformed SSE, timeout, close-before-first-iteration, cancellation, buffered and streaming socket-write failures, and final-flush failure. Every path must leave zero active records and at most one terminal append, without changing response or routing behavior when observation is disabled, saturated, or faulty.

The load-bearing negative controls are:

- Commit success immediately on backend exhaustion: the buffered socket-write test must fail because the authoritative workload result is disconnected.
- Remove the finalization guard: the concurrent/repeated finish test must detect duplicate terminal appends.
- Use a caller request ID as workload identity: the privacy and identity test must fail.
- Omit the admitted transition after lease acquisition: the blocked-after-admission phase test must fail.

## Boundaries

This ticket records a public, sanitized product gap and the accepted implementation contract. It does not prove implementation, deployment, live request visibility, or State completion. Canonical PRD mirroring, approval, parsing, task claims, runtime changes, and operational verification are separate gates.
