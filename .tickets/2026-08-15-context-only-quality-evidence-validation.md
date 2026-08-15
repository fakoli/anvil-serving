# Context-only quality artifacts fail generic evidence validation

**Status:** Open

## Problem

`eval benchmark quality --suite context` writes valid context target records,
including status, API-reported prompt tokens, TTFT, prefill, decode, and usage.
The same artifact also serializes unrelated intelligence, session, and tool
suites as `not_run`. `eval benchmark evidence show` then reports validation
errors because those unrelated suites have no executable checks or attempts.

Likewise, a run that selects only deterministic intelligence/session/tool
checks can retain every attempt and pass threshold while the inspector reports
validation errors for absent aggregate streaming-chat timing fields. These are
not required by the selected workload and should be represented as
not-applicable rather than invalid.

The 2026-08-15 Qwen3.8 SGLang qualification preserved these warnings and used
the artifacts only for their completed target/attempt records. It did not
reinterpret the generic inspector warning as a clean artifact-level pass.

## Required behavior

1. Validate only suites and timing instruments selected by the run.
2. Omit unselected suites or mark them not-applicable without producing an
   artifact validation error.
3. Treat aggregate chat timing as optional for deterministic non-streaming
   quality suites unless the workload explicitly selected that instrument.
4. Keep target-level context failures and missing required measurements
   fail-closed.
5. Make `evidence show` distinguish complete selected evidence from optional
   unselected fields in both human and JSON output.

## Acceptance

- A context-only artifact with two passed targets has zero validation errors.
- A context-only artifact with one failed or missing selected target remains
  invalid or failed.
- An intelligence/session/tool-only artifact with complete threshold-passing
  attempts does not require chat TTFT/E2E aggregates.
- Mixed suites continue to validate every selected component.
- Existing malformed or incomplete artifacts remain rejected.
