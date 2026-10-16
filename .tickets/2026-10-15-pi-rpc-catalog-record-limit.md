# Pi RPC catalog records exceed the default bounded decoder frame

**Status:** Open

## Problem

A selected-model Pi acceptance attempted `get_available_models` before its first
read-tool turn. The runner returned its provider catalog as one JSONL response
record larger than `PiRpcClient`'s 262,144-byte default, so the strict decoder
rejected it before a prompt or model request.

## Boundary

`get_state` already returns the selected provider/model, session state, and idle
status. A selected-route read-turn acceptance therefore does not need the full
catalog. Catalog propagation acceptance does: it computes and validates the full
catalog digest, and must remain a separate workflow.

## Required behavior

1. Provide a supported selected-route acceptance path that uses `get_state`,
   validates the selected provider/model before and after the turn, performs the
   bounded read-tool assertion, and consumes `agent_end` plus settlement proof.
2. Do not issue `get_available_models` in that narrow path.
3. Keep the existing full-catalog propagation check unchanged.
4. Retain the existing `max_record_bytes` constructor bound. If a catalog
   workflow needs a larger bound, require a measured envelope and an explicit
   bounded value; never replace it with an unbounded limit.

## Acceptance

- A fixture with an oversized catalog completes selected-route acceptance
  without requesting the catalog.
- The selected provider/model, exact read result, `agent_end`, and settled state
  are all verified.
- The full-catalog propagation test still requests and validates the catalog.
- Existing decoder tests continue to reject records above their configured
  bound.
