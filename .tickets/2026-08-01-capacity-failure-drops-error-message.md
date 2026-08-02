# Preserve actionable capacity-benchmark failure messages

**Observed:** 2026-08-01

## Problem

A 12-request DeepSeek V4 Flash 0731 capacity run completed 11 requests and
raised one `ValueError`. The console and JSON artifact retained only the
exception class, not its message. That made it impossible to distinguish an
empty reasoning-only stream, invalid endpoint response, client parser defect,
or another request-specific cause from the recorded evidence.

## Resolution

Retain the exception type and a bounded 2,048-character message in both the
console and JSON failure record. Record whether the message was truncated so a
published artifact never implies that a bounded excerpt is complete.

## Acceptance

- Capacity failures retain request index, exception type, message, and
  truncation state.
- Console output contains the same actionable type and message.
- A run where every request fails still writes evidence instead of crashing
  while formatting absent percentile metrics.
- A failed run still writes its atomic evidence artifact and returns nonzero.
- Error messages are bounded before entering a public evidence artifact.
