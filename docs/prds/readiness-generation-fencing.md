# Project: Fence readiness observations across invalidation

## Summary

Make managed quiesce/readmit transitions retain trustworthy readiness evidence
when an HTTP probe overlaps cache invalidation. A probe started before
invalidation must not repopulate the cache or return a usable old readiness
result afterward. Implement this in the existing readiness and transition seams,
without adding a new supervisor or changing route selection.

## Status and evidence

**Status:** Proposed; source-reviewed and one race reproduced with injected
transport, no network or model calls. **Priority:** High.
**Baseline:** Anvil `f50dca489780b95d0cd98dee59cf620618c4ccd1`.

`HttpHealthAvailability.invalidate()` already exists and removes cache entries.
`RoutingBackend.quiesce_tier()` and `readmit_tier()` already call it.
Ordinary quiesce/readmit correctly forces fresh identity checking. Router reload
restarts its container because configuration is startup-read; this is not a
configuration hot-reload project.

The reproduced sequence is narrower:

1. Start a fake successful probe and hold its response with a threading event.
2. Quiesce the tier, which invalidates its readiness cache.
3. Release the old response and join the probe thread.
4. Observe that the cache is populated with the old successful result and the
   original call returns available.

Observed synthetic output:

```json
{
  "cache_repopulated": true,
  "cached_available": true,
  "old_call_returned_available": true,
  "tier_still_quiesced": true,
  "probe_calls": 1
}
```

The admission barrier remained quiesced. This evidence establishes stale
observation publication, not an observed production admission bypass.

Miles supplies the transferable pattern:
[health-check epochs](https://github.com/radixark/miles/blob/d2fc97ce581577e255e494801d7568747d5a10d7/miles/utils/ft_utils/health_checker.py)
and [pause/resume race tests](https://github.com/radixark/miles/blob/d2fc97ce581577e255e494801d7568747d5a10d7/tests/fast/utils/test_health_checker.py).

## Goals

- Invalidation remains effective even when a probe is already running.
- Readmit accepts only current exact-identity or validated dynamic-metadata evidence.
- Unrelated tiers and existing streams continue operating during a transition.
- Tests prove behavior with controlled scheduling, without sleep-based races.

## Non-Goals

- Automatic restarts, retries, health hysteresis, or fallback model selection.
- Discovering a same-URL engine replacement without any lifecycle notification.
- Replacing the gateway, changing external transition commands, or new runtime dependencies.
- Fixing recipe discovery, GPU ownership, or native-offload cleanup.

## Requirements

- R001: Every cached probe result is associated with the readiness generation
  current when that probe began.
- R002: `invalidate(tier_id)` atomically advances that tier's generation and
  clears its cached result, including when the cache was already empty.
- R003: `invalidate(None)` fences all probes already in flight, including a
  tier that had never produced a cached result.
- R004: A response from an earlier generation is discarded. It cannot update
  the cache or return an available result to its initiating caller. Return a
  bounded unavailable result with reason `probe_invalidated`; do not silently
  launch a replacement HTTP request from that completion path.
- R005: The existing same-tier single-flight behavior remains intact. A check
  while an invalidated old probe owns its lock returns `probe_pending` when
  there is no current result. Once that flight finishes, a later check may
  start the new generation's probe.
- R006: Concurrent direct `probe_now()` calls cannot let an earlier-started
  probe overwrite a later-started observation. Use a per-tier issuance counter
  in addition to the generation; a superseded completion returns unavailable
  with reason `probe_superseded`.
- R007: Old failures cannot overwrite newer success, just as old success cannot
  overwrite newer failure. Freshness timestamps describe the accepted probe,
  not the discarded completion.
- R008: Quiesce still closes admission before cache invalidation. Readmit still
  invalidates before verification, remains quiesced on pending/invalidated
  evidence, and retains a successful current identity result in the cache.
- R009: No global lock is held during HTTP I/O. Invalidating one tier does not
  invalidate another. Auth handling, redirect/proxy policy, response limits,
  configured timeouts, dynamic metadata, and stream-lease release remain intact.
- R010: New state is bounded by configured/observed tier keys; invalidation does
  not accumulate one record per generation. New reasons contain no URL,
  credential, response body, or model-generated text.

## Implementation map

| Existing file | Read these seams | Intended change |
| --- | --- | --- |
| `anvil_serving/router/availability.py` | `HttpHealthAvailability.__init__`, `probe_now`, `check`, `invalidate`, `cached` | Generation and issuance fencing under the existing cache lock |
| `anvil_serving/router/serve.py` | `quiesce_tier`, `readmit_tier`, `transition_status` | Preserve ordering; change only if an integration test proves it necessary |
| `anvil_serving/router/front_door.py` | `_handle_transition` and management mutation semaphore | Regression coverage, not a new management API |
| `tests/router/test_availability.py` | Fake response/opener and injected clocks | Focused deterministic cache tests |
| `tests/router/test_transition_integration.py` | Guarded readmit and live stream lease tests | Real checker integration rather than only fake invalidation hooks |

Implementation sketch: maintain a global epoch plus per-tier epochs and issuance
counters. Snapshot `(global_epoch, tier_epoch, issued_sequence)` under
`self._lock`, release it for the existing bounded probe, then compare the
snapshot while holding the lock before publication. Invalidation changes the
epoch even if no cache entry exists. Preserve counters or advance the global
epoch when clearing maps so a reused numeric value cannot make an old response
look current. Do not remove/recreate an in-use probe lock.

Keep the failure result separate from the observed response: replacing only the
cache assignment is insufficient if the caller still receives stale success.
Use an unavailable result without runtime metadata on rejection.

## Features

### F001: Fence probe publication and returned readiness

**Requirements:** R001, R002, R003, R004, R005, R006, R007, R010

### F002: Preserve transition and admission behavior

**Requirements:** R008, R009

## Tasks

### T001: Capture the invalidation race in a failing test

**Feature:** F001
**Priority:** high
**Likely files:** tests/router/test_availability.py

Use `threading.Event` to block the fake opener or `_probe`. Start a check,
wait for entry, invalidate, release, and join with a bounded timeout. Always
release/join in `finally` so a failing assertion cannot hang the suite.

**Acceptance criteria:**

- The baseline fails an assertion that stale completion cannot repopulate cache.
- A second assertion catches stale success returned directly to the old caller.
- The test makes no real endpoint request and uses no scheduling sleeps.

**Verification:**

- `python scripts/run_tests.py tests/router/test_availability.py -x -q`

### T002: Add generation and issuance fencing

**Feature:** F001
**Priority:** high
**Dependencies:** T001
**Likely files:** anvil_serving/router/availability.py, tests/router/test_availability.py

Implement the smallest state addition described above. Cover global invalidation,
empty-cache invalidation, repeated invalidation, old failure after new success,
and reversed completion of direct probes. Preserve the existing API signatures.

**Acceptance criteria:**

- R001 through R007 and R010 have named tests.
- A delayed tier A probe does not delay a tier B probe.
- Temporarily disabling the epoch comparison causes the race test to fail.
- Restore the negative-control change before proceeding.

**Verification:**

- `python scripts/run_tests.py tests/router/test_availability.py tests/router/test_dynamic_upstream_metadata.py -x -q`

### T003: Verify transitions with the production checker

**Feature:** F002
**Priority:** high
**Dependencies:** T002
**Likely files:** tests/router/test_transition_integration.py, anvil_serving/router/serve.py

Inject the real `HttpHealthAvailability` with fake I/O into
`RoutingBackend`. Cover an old probe crossing quiesce and a readmit attempted
while that probe is still outstanding. Then release it and explicitly retry
readmit with current passing/failing identity responses.

**Acceptance criteria:**

- Pending or invalidated evidence never readmits.
- A fresh exact identity can readmit and stays cached.
- A mismatched identity stays quiesced; another alias remains usable.
- Existing streams drain and release their lease exactly once.

**Verification:**

- `python scripts/run_tests.py tests/router/test_transition_integration.py tests/router/test_streaming_relay.py -x -q`

### T004: Document the guarantee and complete regression checks

**Feature:** F002
**Priority:** medium
**Dependencies:** T003
**Likely files:** docs/THIN-CAPABILITY-GATEWAY.md, docs/adr/0018-router-transition-safety.md

Explain the new reasons and the need for an explicit lifecycle invalidation.
Avoid claiming arbitrary engine-restart detection or automatic recovery.

**Acceptance criteria:**

- Documentation distinguishes observation freshness from admission and qualification.
- The complete router test suite passes.
- The handoff includes the race test, negative control, and remaining limitations.

**Verification:**

- `python scripts/run_tests.py tests/router/ -x -q`
- `python -m mkdocs build --strict`
- `git diff --check`

## Acceptance Criteria

| Scenario | Required result |
| --- | --- |
| Probe crosses tier/global invalidation | No cache publication; unavailable return |
| Old failure arrives after current success | Current success retained |
| Two direct probes finish in reverse order | Earlier-issued completion cannot win |
| Readmit during old flight | Refused; admission stays quiesced |
| Fresh readmit after old flight settles | Exact identity decides acceptance |
| Different tier probes during a blocked probe | Completes independently |

## Risks

- Holding a global lock over network I/O would serialize unrelated routes.
- Clearing generation counters can create an ABA race; the global epoch avoids it.
- Returning old success despite rejecting its cache write leaves half the bug.
- This change must not relax fail-closed admission to tolerate transient health failures.

## Assumptions

### A001: Existing explicit invalidation is the lifecycle boundary.

**Rationale:** The production checker and managed transition callers already
exist. Extending their contract avoids an unnecessary controller protocol.
**Requirements:** R002, R003, R008

## Open Questions

- Parked beyond v1: a durable engine-instance identity for replacements that do
  not pass through managed transitions. Do not invent a header or infer a
  restart from a model name.

## Rollout and rollback

No operator configuration migration is required. Ship through the normal router
release path; installation/restart is a separate deployment action. Reverting
the code restores the old cache behavior without changing manifests or evidence.
Production fault injection is not required to complete this source change.
