# Keep scoped operator routes separate from the router data plane

Status: implementation under independent review
Priority: P1
Date: 2026-09-05
Task: fleet-node-enrollment:T010

## Reason for change

Unified workload reads need a per-client operator grant; the existing shared
chat/media token must not gain that authority. The optional policy is loaded
once, and malformed/missing policy disables new scoped surfaces without
changing existing legacy authentication. No workload endpoint ships in this
prerequisite task.

## Review findings to close

- An initial registry admitted `/health`, `/v1/models` and the request-trace
  namespace, allowing registered operator callbacks to shadow existing routes.
- An injected sequence reporting length one yielded nine accepted routes,
  defeating the eight-entry bound. Malformed method types raised raw TypeError.
- The `build_server` registry-forwarding seam was missing even though direct
  `make_server` tests passed.
- Operator callbacks bypassed existing concurrency pools; add a bounded
  read-only pool, no-store responses, and denial before callback or body parsing.
- Extend actual HTTP/socket tests for POST withheld bodies, auth-off with no
  operator policy, per-request principal reset, and every CLI/server forwarding
  seam. Preserve ordinary chat, streaming, media and transition behavior.

Each finding was checked against the current code; closure needs regression
tests, independent re-review and post-commit claim-bound evidence. No live
policy, token, controller, model or route has changed.

The next independent pass reproduced ambiguous duplicate/mixed credential
headers reaching callbacks, and response-delivery slots being released before
slow clients consumed the response. Corrections add a scoped-only exactly-one
credential extractor (leaving legacy extraction unchanged) and retain the
four-slot lease through response headers, body delivery and flush. Raw header
permutations, four blocked deliveries versus a fifth client, and write-failure
recovery now pass. Initial and corrective reviews cover all eight angles;
77 auth/CLI/core tests, 36 streaming/front-door regressions and Ruff passed.
Post-commit proof and acceptance are the remaining task gate, not live enablement.
