# Ship log: turning the usage findings into shipped work

Running log for the autonomous run that began on 2026-11-26. It records each
pull request, what was deployed, the numbers before and after, and any
rollback. Findings that motivate the work are in
[serving-usage.md](serving-usage.md). Hosts, callers, and model identities are
withheld as in that document.

Judgment calls in this run (order, design, ship and rollback decisions) are
delegated by the owner to a Fable advisor and reviewer. That reviewer shares a
model family with the author, so it is a fresh-context check, not the
independent review that the repository's own working rules ask for. Each entry
says who reviewed it.

## Baseline (before any change)

Thirty days of gateway accounting ending 2026-11-26, and 14 days of engine
metrics.

| Measure | Baseline |
| --- | --- |
| `llm.primary` success rate | 94.4% (10,412 of 11,035) |
| `llm.primary` refused before dispatch | 4.4% (490) |
| `llm.primary` client disconnects | 1.2% (130) |
| `llm.primary` latency p50 / p95 / p99 | 2.4 s / 37.4 s / 82.4 s |
| `llm.secondary` success rate | 2.9% (1 of 34) |
| Engine time to first token p50 / p95 | 0.48 s / 3.5 s |
| Engine arrival to admission p95 | 0.28 s |
| Input tokens served from prefix cache | 90.5% |
| Conversation-cache budget in use | 100% in every sample |
| Usage records with admission wait, first-content time, or error code | 0 of 58,667 |

## Advisor rulings (2026-11-26)

The Fable advisor reviewed the backlog against the code and ruled as follows.
These rulings set the plan below.

- **Order.** Item 5 first, since it is documentation only and settles the two
  ADRs before item 1 amends them. Then the accounting instrumentation from
  items 2 and 4, so the redirect is measurable. Then item 1. Then the fix for
  item 2, once the recorded cause is known. Item 3 is measurement only.
- **Item 1 design.** An explicit, opt-in table mapping one alias to one
  redirect target, decided once before dispatch. It triggers only on a
  completed negative readiness result for the source tier. It never triggers on
  quiesce, capacity, context, media or tool refusals, a pending probe, or
  anything after dispatch. The caller's credential must already be granted the
  target alias, or the request fails as it does today. One hop only. The
  response names the alias that served, and the decision log records both.
- **Items 2 and 4.** Two changes. First populate admission wait, first-content
  time and error codes, and make this build able to read three new refusal
  codes without emitting them. Emit the new codes only in a later change, so a
  rollback stays read-compatible. Rate-limit and authorization refusals happen
  before a usage record exists, so they get no code.
- **Session identifier: deferred to the owner.** It conflicts with the
  accounting privacy contract in `docs/ROUTER-USER-USAGE.md` and needs a schema
  change.
- **Item 3: measure first, no engine change in this run.** A full
  least-recently-used cache is its steady state, not evidence of harm. Any
  engine cache change or restart of the primary serve is left to the owner.
- **Ship gate.** After each gateway deploy, watch 200 `llm.primary` requests
  including one morning burst before the next deploy. Roll back at once on any
  accounting failure or HTTP 500. Roll back if success falls under 90%,
  pre-dispatch refusals exceed 8%, median latency exceeds 3.5 s, or 95th
  percentile latency exceeds 55 s.

## Deploy constraint found on 2026-11-26

The supported gateway cutover stages protected, root-owned files and runs the
maintenance console. This run's session cannot become root: `sudo` is blocked
by the session sandbox. Stopping and starting the gateway container directly
would bypass the custody procedure, so it was not attempted.

Consequence: this run can take each change through branch, tests, pull request,
review and merge, but **cannot deploy to the live gateway**. Deploys and the
watch period after each one are left for the owner. The backlog table marks
them. Nothing in this log has been deployed unless an entry says so.

## Backlog and status

| # | Item | Status |
| --- | --- | --- |
| 5 | ADR-0034 and ADR-0039 amendments match the replica code | PR 834 reviewed, waiting on CI |
| 2+4a | Accounting: populate admission wait, first-content time, error codes; read new codes | In implementation |
| 1 | `llm.secondary` redirects to `llm.primary` when its model is not served, with an ADR amendment | Queued behind 2+4a |
| 2 | Fix what the recorded refusal cause shows | Blocked on data from 2+4a |
| 4b | Accounting: emit the new refusal codes | Queued behind 2+4a deploy |
| 4c | Accounting: session identifier | Left for the owner |
| 3 | Conversation-cache budget | Measure only; engine change left for the owner |

## Entries

### 2026-11-26 — Item 5, PR 834 (documentation only)

- **Change.** A dated status note under the 2026-09-05 replica amendments in
  ADR-0034 and ADR-0039: replica tiers are implemented (#472), a tier may have
  2–16 members on one host, and member selection is `round_robin` or
  `capacity`. No decision or invariant changes, and nothing to deploy.
- **Checks.** Docs audit passed locally. First CI run: one required job failed
  on `tests/agent/test_service.py::test_posts_beyond_the_inflight_cap_get_503_before_their_body_is_read`
  (`assert 503 == 200`). The PR touches no code and `main` passes the same
  job, so this is treated as a timing-sensitive test and the job is rerun, not
  bypassed.
- **Review.** Fresh-context Fable review approved it after checking each
  claim against the code, with five wording fixes. All five were applied: the
  note now says no member is reselected once one is selected, credits the
  capacity strategy to its own design document rather than the 2026-09-05
  amendment, and cites the change as PR 472.
- **CI.** Second run failed a different test in the same module, on macOS:
  `tests/agent/test_service.py::test_logs_carry_no_content`. It reads captured
  log output before the server has written its line, so it is also
  timing-sensitive. The failed job was rerun.
- **Status.** Ready for review, waiting on CI. Not merged.
