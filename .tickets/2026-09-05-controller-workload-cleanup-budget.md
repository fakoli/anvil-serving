# Account for successful response cleanup in controller read budgets

Date: 2026-09-05
Status: fix-forward source integrated; consolidated acceptance pending

While grounding dashboard reuse, a deterministic public fleet-reader probe at
`30d054c1` returned complete after an injected final POST response close advanced
the monotonic clock from zero to eight seconds. Its stated total budget is seven
seconds. The exact output was `returned_status=complete elapsed=8.0 budget=7.0`.
Health, POST and bounded reads stayed at zero; only successful final cleanup
advanced the clock. No live request was made.

`_BudgetedResponse.__exit__` records throwing close failures but never checks the
deadline after a successful close. Add the same monotonic completion validation
after response cleanup and record any close/clock/deadline failure in the existing
private budget-failure state. Do not skip cleanup or claim to preempt blocking I/O.
The synchronous reader may finish late, but it must reject that late result.

Cover both node and fleet final close, exact deadline, regression/nonfinite clock,
successful within-budget cleanup and no duplicate close. Preserve fixed public
errors and the two/seven-second budgets. Keep the shared transport unchanged.

Implemented by candidate `8943a4f3`, evidence `EVC9807BFA`. Its focused
postcommit gate passed 104 tests and Ruff. Negative control before the fix
reported seven failures and two passes for the new cleanup cases. Both readers
now include successful response cleanup in their completion budget, close once,
and reject late/invalid-clock results with fixed errors. No live call or
deployment occurred; final batch acceptance remains pending.
