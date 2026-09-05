# Capacity scheduler terminal lifecycle regression matrix

Status: implementation queued; consolidated acceptance pending.

## Why

Scheduler T004 integrates compound tier/member admission into the existing
RoutingBackend and close-aware iterator. The original T007 wording asked for
another lease attachment inside RelayBackend/SSE, which would duplicate the
owner rather than exercise it. Existing qualified-replica terminal fixtures
cover round robin but must also cover explicit capacity scheduling.

## Change

Clarify T007 as the real-relay ordinary/SSE and Responses terminal regression
matrix. Parameterize the established fixtures over both strategies, inject
completed pressure, keep real admission counters, and verify exactly-once
release, reconciled counts and no peer dispatch across every terminal path.
No production ownership change or live service is implied. Any actual defect
discovered gets its own scoped implementation record.

## Verification

- `python scripts/run_tests.py tests/router/test_streaming_relay.py tests/router/test_responses.py -x -q`
- `python -m ruff check anvil_serving/router/backends/relay.py anvil_serving/router/backends/sse.py tests/router/test_streaming_relay.py tests/router/test_responses.py`
- `git diff --check`

Per the operator's batch cadence, ordinary regression proof accompanies the
candidate; formal acceptance occurs with the completed feature batch.
