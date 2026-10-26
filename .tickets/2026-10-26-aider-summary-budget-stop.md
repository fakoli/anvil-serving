# Aider summary worker leaks expected budget stops

**Status:** In progress

## Problem

The benchmark adapter bounds Aider summary calls with the same request budget as task work. When a background chat-history summary reaches an exhausted call budget, the adapter raises `BudgetStopped`. The pinned Aider worker catches `ValueError` only, so this expected stop escapes its thread, prints a traceback, and can leave summary bookkeeping pointing at an empty result.

The campaign should still finish as `budget_exhausted` with the exact request accounting. Summary compaction is advisory and must not trigger another request after a budget stop.

## Acceptance

- Catch only `BudgetStopped` at the adapter's summary-worker boundary.
- Preserve the unsummarized message list so Aider bookkeeping does not replace it with an empty summary.
- Keep the outer run classified as budget exhausted and preserve completed request counts; do not send a fallback summary request.
- Preserve propagation of unrelated summary exceptions.
- Cover the blocked background-summary path with a focused offline test.

## Evidence

Offline Aider run artifacts show 12 admitted model requests completed, then the summary worker's next request was rejected at the shared call cap. No model response or grader result was missing from the request ledger.
