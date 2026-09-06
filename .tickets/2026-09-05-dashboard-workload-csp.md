# Dashboard workload script CSP repair

## Reproduced defect

A local Edge 1440x1000 smoke of the packaged dashboard reported that loading
same-origin `/workloads.js` violated `script-src 'unsafe-inline'`. The
workload panel remained disconnected and did not issue workload requests.

## Candidate repair

The telemetry response CSP now adds only `'self'` to `script-src`; all other
directives and response security headers remain unchanged. A loopback HTTP
regression checks the served document and packaged script bytes plus the exact
directive sets.

## Rendered regression evidence

After repair, Playwright 1.62.1 with installed headless Edge exercised the
real packaged page and HTTP workload service with an injected synthetic reader.
At 1440x1000 and 390x844, connect displayed one populated and one unavailable
node with partial/unknown-omission metadata; an owner filter removed the record
and clearing it restored the record. Disconnect cleared data; selecting
Overview hid the panel. Credential input cleared after connection and both
browser storage collections remained empty. There was no horizontal overflow,
runtime exception, CSP violation or warning. The existing optional favicon
request returned 404; it did not affect the flow.

The initial browser run with the old header made no workload requests; the
same corrected flow made three bounded requests. These are synthetic UI tests,
not real controller/fleet acceptance. Temporary screenshots and harnesses stay
outside the repository. A separate full-suite checkpoint before this CSP repair
passed 6916 tests with 10 skips at 97614c4a; it does not prove this later change.

## Observed Windows transport test interruption

The first focused run encountered ConnectionAbortedError / WinError 10053 at
tests/observability/test_api.py:282 in
test_reserved_workload_rejects_unread_body_headers_and_post_before_service
(Transfer-Encoding: chunked parameter), before the follow-up POST status line.
The direct rerun passed 27 tests. Twenty subsequent consecutive repeats of the
four framing cases passed (80 cases). The original WinError 10053 cause remains
unproven; no exception-masking or transport patch was made. This observation
remains separate from the reproduced CSP cause.

The CSP candidate's postcommit gate at `781708d5` (`EV321129F4`) passed 27
focused tests and Ruff. Its synthetic browser evidence above is not a live
controller/fleet acceptance result.

## Source acceptance and deployment boundary

Consolidated source acceptance is complete; the integrated runtime checkpoint
`f964a81e` passed 7185 tests with 21 skips. See
`.tickets/2026-09-05-router-fleet-merge-checkpoint.md` for the final review and
publication gates. Package release and live deployment remain deferred.
This source repair does not claim that any deployed dashboard has changed.
