# Fail fast when a managed recipe container exits during readiness

**Observed:** 2026-08-01

## Problem

`models recipes load` waits the full 600-second HTTP-readiness timeout even when
the just-created, exactly owned container has already exited. During the TP2 Qwen
launch, vLLM failed before initialization, but the mode transaction could not begin
its guarded rollback until the entire timeout expired.

The multiplexer backend already implements the desired behavior by polling its
child process and failing immediately when the process exits. The recipe lifecycle
path only calls the generic HTTP `_await_healthy` helper and therefore has no
equivalent exit-state gate.

## Impact

- Deterministic startup errors add ten minutes to each unattended qualification
  attempt before managed rollback can run.
- The actionable container log is available immediately but the CLI does not print
  its recovery guidance until the timeout ends.
- Exclusive mode remains quiesced longer than necessary after a failed candidate.

## Proposed resolution

Add a recipe-specific readiness loop that checks both declared HTTP health and the
exact container's Docker state. Return immediately when the owned container reaches
an irreversible non-running state such as `exited` or `dead`, while preserving the
bounded wait for legitimate model loading states.

## Acceptance

- A hermetic test proves `exited` and `dead` fail before the first sleep.
- `created`, `restarting`, and `running` continue polling until health or timeout.
- Docker inspection errors remain explicit and do not get misreported as a model
  failure.
- Failure output includes the observed container state and the existing bounded-log
  and managed-unload recovery commands.
