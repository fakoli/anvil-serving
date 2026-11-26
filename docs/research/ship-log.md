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

## Backlog and status

| # | Item | Status |
| --- | --- | --- |
| 1 | `llm.secondary` redirects to `llm.primary` when its model is not served, with an ADR amendment | Not started |
| 2 | Record why `llm.primary` requests are refused before dispatch, then fix it | Not started |
| 3 | Relieve the conversation-cache budget | Not started |
| 4 | Accounting: session identifier, admission wait, first-token time, error codes | Not started |
| 5 | ADR-0034 and ADR-0039 amendments match the replica code | PR 834 open, in review |

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
- **Review.** Fresh-context Fable review requested.
- **Status.** Open as a draft. Not merged.
