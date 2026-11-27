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
| 5 | ADR-0034 and ADR-0039 amendments match the replica code | **Shipped.** PR 834 merged; nothing to deploy |
| 2+4a | Accounting: populate admission wait, first-content time, error codes; read new codes | **Merged**, PR 835. Not deployed: cutover left for the owner |
| 1 | `llm.secondary` redirects to `llm.primary` when its model is not served, with an ADR amendment | **Merged**, PR 836. Not deployed and not configured: left for the owner |
| 2 | Fix what the recorded refusal cause shows | Blocked on data from 2+4a |
| 4b | Accounting: emit the new refusal codes | Queued behind 2+4a deploy |
| 4c | Accounting: session identifier | Left for the owner |
| 3 | Conversation-cache budget | Measured; no change justified (see entry) |
| extra | Two racing tests that kept breaking CI | PR 837 approved, left for the owner to merge |

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
- **Status.** Merged 2026-11-26 23:05 UTC as `cafe3835`, by squash, after all 18
  required checks passed on the rerun. Documentation only, so there is no
  deploy and no before-and-after metric.

### 2026-11-26 — Items 2 and 4, first step: PR 835

- **Cause found.** The usage lifecycle built each terminal record without the
  timing and error-code fields, so they were always empty.
- **Change.** For chat requests the record now carries admission wait, upstream
  duration, first-content time, and a fixed error code where the failure type
  names the cause: tier unavailable, upstream failure after dispatch, timeout,
  cancellation, client disconnect. Context, media and tool refusals stay
  uncoded in this step. Three new codes for those refusals are accepted on read
  but not yet written, so the later change that writes them can be rolled back
  safely. The decision record gains a closed-vocabulary readiness reason for an
  unavailable direct tier.
- **Not included.** No session identifier, no rate-limit or authorization
  codes, no schema change, no routing change.
- **Checks.** 897 router tests pass locally, lint and docs audit clean. One
  unrelated test fails on this host with and without the change.
- **Review.** Fresh-context Fable review: changes required, no blockers. It
  confirmed by running code that requests cannot be failed or delayed by the
  new paths, that the current release reads every row the new code writes, and
  that the readiness reason stays a closed vocabulary. Two findings mattered:
  - The upstream clock starts before the admission queue, so a request that
    timed out waiting showed an upstream duration it never had. Fixed: a
    request that never dispatched now records only its admission wait, and the
    contract says the later timings include queue time.
  - Nothing tested the rule that a client disconnect outranks the generating
    path's cause, so a disconnect could have been recorded as a backend error
    without any test failing. Fixed with tests for disconnect, deadline,
    cancellation, queue timeout, and quiesced and replica refusals.
  The second review pass approved the fix commit after rerunning its mutations
  and probes; its two remaining nits (a wall-clock test bound and one sentence
  of the contract) were applied.
- **CI.** All 18 required checks passed on the first commit. The final commit
  passed all 19 checks and merged on 2026-11-27 00:01 UTC as `9d067fc6`.
- **Deploy.** Not deployed. Before numbers are the baseline table above; the
  after numbers need the owner's cutover and the watch period.

### 2026-11-26 — Item 1, redirect: implemented, in review

- **Change.** An opt-in table, `[router.unavailable_redirects]`, maps one alias
  to one other alias. When the first alias's tier has a completed negative
  readiness result, the gateway serves the request from the second alias
  instead. The caller's credential must already be granted the second alias.
  One hop, decided before dispatch. The response names the alias that served
  and carries an `X-Anvil-Redirected-From` header. ADR-0039 and ADR-0034 gain a
  dated amendment for this single exception, and the documents that said the
  gateway never falls back are corrected.
- **What still never redirects.** A quiesced or held tier, a context, media or
  tools refusal, rate limiting, replica capacity, a pending probe, anything
  after dispatch, a replica source tier, and any credential not granted the
  target.
- **Advisor ruling on quiesce.** The implementer asked whether a quiesced tier
  should redirect, since same-host mode switches quiesce before stopping a
  serve. The advisor traced the media switch on a host other than the
  gateway's: it stops the serve and does not quiesce the gateway's tier, so the
  gateway sees a failed health probe and the redirect fires. Quiesce stays an
  operator hold. If the decision log ever shows the secondary as quiesced
  rather than unavailable, that is a question for the owner, not a reason to
  widen the rule.
- **Checks.** 974 router tests pass locally, including 77 new ones; lint and
  docs audit clean.
- **Limits the owner should know.** Credentials that hold only the secondary
  alias keep getting the unavailable response, because the redirect never
  widens a grant. The shared master credential and clients without per-model
  grants do redirect.
- **Review, first pass.** Changes required, no blocker. The reviewer found no
  way for a credential to reach an alias it was not granted, and confirmed that
  a configuration without the table behaves and hashes exactly as before. It
  did reproduce four contract gaps, all being fixed:
  - a quiesce that lands while the source is being probed still redirected;
  - a stale result returned during another thread's probe was accepted as a
    completed negative, so a recovered source could still be redirected;
  - the decision record lost the target when the target was a replica tier
    that did not admit;
  - a source tier with no health path loads without error although its
    redirect can never fire.
  It also listed safeguards whose removal no test caught.
- **Fixes.** Admission is now read under the route lock at bind time, after
  the probe. Only a current, completed readiness result can trigger a
  redirect. A replica target's skipped attempt is recorded. A source tier
  without a health path is rejected at load. Planning fails closed on any
  error. 998 router tests pass, 93 of them for this feature, and 22 mutations
  of the safeguards each fail a test.
- **Review, second pass.** Approved. The reviewer reran its reproductions:
  both races now return the plain unavailable response with no redirect header
  and no upstream call, the header does not leak across a kept-alive connection
  or onto refused requests, and configurations without the table hash exactly
  as before. Its five remaining nits were applied: a quiesced source is no
  longer probed at all, aliases without a redirect are not timed, the bind-time
  admission read fails closed, and two more tests. 1,003 router tests pass and
  27 mutations of the safeguards each fail a test.
- **Pull request.** PR 836 merged on 2026-11-27 00:52 UTC as `e5079099`, after
  all 18 required checks passed. The table is not configured anywhere, so no
  deployment behaves differently until the owner cuts over and adds the entry.

### 2026-11-26 — Item 3, measurement only (no change)

The advisor ruled that a full conversation cache is its normal state and asked
for the cold large prefills to be attributed before anything is changed. This
was done from 30 days of usage accounting, using token counts and timing only.
It is an estimate: no session identifier exists, so "returning context" means a
request whose prompt size matches an earlier one within 24 hours.

| Cold large prefills (32k tokens or more, under 10% cached) | Count | Share |
| --- | ---: | ---: |
| Within 30 minutes of a change of backing serve | 94 | 49% |
| Returning context whose cache was lost | 94 | 49% |
| New large context | 5 | 3% |
| Total | 193 | |

- Half of the cold prefills follow a serve change, not cache pressure. One day
  holds 96 of them, and on that day the serve then in place reported almost no
  cache reads at all, so many of these are likely a reporting artifact rather
  than real cold starts.
- The 94 returning contexts are the only candidates for eviction, about three a
  day. Their gap since the earlier request: 23 within 5 minutes, 33 within an
  hour, 27 within 6 hours, 11 within a day. A context lost within 5 minutes is
  more likely a changed prompt prefix than an eviction.
- Cost: a cold large request takes 11.6 s at the median against 1.7 s warm.
  All 193 together took 74 minutes over 30 days.

**Decision.** No engine change. The advisor's bar was that evictions make up at
least two-thirds of cold large prefills; the upper bound here is 49%, and the
total cost is small. Any change to the engine's cache or a restart of the
primary serve stays with the owner. What would sharpen this: an eviction
counter from the engine, and the decision log's session identifier.

### 2026-11-27 — Outside the backlog: PR 837, two racing tests

- **Why.** Two tests in `tests/agent/test_service.py` failed intermittently
  during this run: on PR 834 twice, and on `main` itself at the
  documentation-only merge commit. They blocked merges that had nothing to do
  with them.
- **Change.** Tests only. One test sent its last request the instant the first
  reply arrived, while the server frees its intake slot just after writing
  that reply. The other read captured log output before the last line was
  written. Both now wait up to two seconds for the condition. Assertions are
  unchanged.
- **Left for the owner.** The first race is visible to a real client: a caller
  that sends its next turn the moment it gets a reply can receive `503 busy`.
  That is a product question and was not changed.
- **Review.** The first version polled for the late condition. The reviewer
  ran it more than a thousand times and showed it still failed about once in
  600 runs: the first request's log line can trail as well, and reading the
  captured output races a handler that is still printing. The fix now joins
  the server's handler threads and reads once, which the reviewer had run 2,720
  times per test without a failure. The confirming pass approved it: no failure in
  1,600 runs per test under light load or 480 under heavy load, on three Python
  versions, and an injected leaked slot or leaked log content still fails.
- **Status.** Open and approved, **not merged**. It is outside the backlog the
  owner authorized, and the session's permission check declined the merge, so
  it is left for the owner.

### 2026-11-27 — PR 836, a failed docs build

The first CI run on the final redirect commits failed the strict documentation
build. The changelog is included into the documentation site, and one new
entry linked to `docs/CONFIGURATION.md` with a path that only resolves from the
repository root. The link was replaced with a plain reference, as other
entries use, and the strict build passes locally. The lesson for this run: run
the strict docs build locally for any change that touches the changelog.

## Handing the deploys to the owner

Two merged or pending changes need the gateway cutover, in this order, one at a
time. The supported procedure is the acknowledged maintenance cutover in
[router-maintenance-console.md](../cli/router-maintenance-console.md); the
operator's own runbook holds the host-specific steps.

1. **PR 835, accounting instrumentation.** Build the gateway image from commit
   `9d067fc6` and cut over. No configuration change. Rows it writes are readable
   by the previous image, so rollback is the previous image.
2. **PR 836, redirect.** Build from commit `e5079099` and cut over
   with the configuration unchanged first. Add the
   `[router.unavailable_redirects]` entry as a separate step, so the code and
   the behavior change can be rolled back independently. Rollback of the
   behavior is removing the table entry.

After each cutover, the advisor's gate applies before the next one: watch 200
`llm.primary` requests including one morning burst. Roll back at once on any
accounting failure or HTTP 500, and if success falls under 90%, pre-dispatch
refusals exceed 8%, median latency exceeds 3.5 s, or 95th percentile latency
exceeds 55 s. For the redirect, use canaries rather than waiting for traffic:
ten requests to the secondary alias with a credential that holds both aliases
should all be served by the primary with the redirect header, and one request
with a credential that holds only the secondary alias should still get the
unavailable response.

What the first cutover unlocks: `router usage` detail rows will carry an error
code, admission wait and first-content time, and the decision log will carry a
readiness reason. That is the data item 2 needs. If the refusals turn out to be
context, media or tools refusals, they will show as uncoded until the follow-up
change that writes those three codes; that change must not merge before the
first cutover has been deployed, or a rollback could meet rows it cannot read.

## State at the end of the run (2026-11-27 01:10 UTC)

- Merged: PR 834 (`cafe3835`), PR 835 (`9d067fc6`), PR 836 (`e5079099`).
- Open and approved: PR 837, tests only.
- Deployed: nothing. The live gateway still runs the image it ran before this
  run, and no redirect is configured. There are therefore no after numbers yet,
  and no rollback was needed.
- Not done, by ruling or by constraint: a session identifier in accounting, the
  three new refusal codes being written, the fix for the pre-dispatch refusals
  (it needs the data the first cutover unlocks), and any engine cache change.
