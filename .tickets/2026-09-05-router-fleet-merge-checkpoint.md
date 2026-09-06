# Router and workload batch merge checkpoint

Status: consolidated source acceptance complete; PR CI and merge are the
remaining publication gates. Package release and live deployment are deferred.

The operator's stop point is explicit: finish this source batch, create and
merge its PR, summarize completed/remaining work, then stop. Do not continue
with enrollment implementation, controller recovery or deployment in this run.

## Completed source and task acceptance

The dedicated delivery State now contains 132 done tasks and nine unfinished
fleet tasks. This batch applied 101 previously pending candidates with strict
evidence gates, in dependency order, as `codex-user-delegated`; the other 31
retain their earlier accepted history. This is delegated agent acceptance under
the operator's 2026-09-05 authorization, not an invented human review.

| PRD partition | Done / total | Source outcome |
| --- | --- | --- |
| controller-diagnostics | 11 / 11 | Bounded inspect/log/status surfaces, CLI/MCP contracts and private error envelopes |
| qualified-replica-sets | 21 / 21 | Closed same-host equivalent replica sets, exact identity, one-shot routing, lifecycle guards and bounded evidence |
| replica-capacity-scheduler | 16 / 16 | Atomic tier/member admission, deterministic scheduling, cached pressure and independent member drain/readmission |
| workload-visibility | 50 / 50 | Scoped canonical node/fleet reads, store/managed projections, CLI/controller MCP and dashboard |
| workload-contract-repairs | 18 / 18 | Integration, timestamp, UI semantic, identity, command metadata and fixture repairs |
| fleet-node-enrollment | 16 / 25 | Authorization plus inert target/request/result, package, permission and anchored-file primitives only |

No acceptance was applied to the unfinished nine tasks. Bootstrap primitives
are not an executable enrollment workflow and do not prove installed identity.

## Consolidated verification

Runtime code checkpoint: `f964a81ef6c839d2a93f69d3b7938703c95b662f`.
Subsequent checkpoint edits are documentation/evidence only.

- `python scripts/run_tests.py tests/ -x -q`: 7185 passed, 21 skipped,
  295.68 seconds on Windows/Python 3.13.13.
- `python -m ruff check .`: passed.
- Full CLI-reference audit: 984 files, zero violations, inventory/generated/nav
  current; canonical command and skill-related tests also passed.
- Strict MkDocs, tracked Markdown links and artifact-derived recipe report:
  passed. No new model benchmark or recommendation was claimed.
- Source distribution/wheel build, Twine metadata check and fresh no-dependency
  wheel install/CLI/package-data smoke: passed. Nothing was published.
- Clean tracked Git snapshot: pinned Gitleaks and semantic scanner passed with
  zero findings; semantic intentional-positive self-test passed. This is not a
  full-history secret audit.
- Windows CLI hygiene scanner: 20 pre-existing advisories, all in unchanged
  files; no new changed-file finding. The same scanner ran under WSL after the
  native Git Bash per-line scan proved too slow.
- Whole PR diff whitespace check: passed.

Consolidated independent router/scheduler, workload/dashboard and
controller/bootstrap reviews covered the completed source. Confirmed temporal,
browser semantic and replica/controller identity findings were repaired and
rechecked. The final closure at the runtime checkpoint returned SHIP: 21
critical transport/loopback/budget tests and 79 fixture tests passed; deliberately
weakened duplicate/overflow guards dispatched and failed their assertions.
The workload/UI recheck returned SHIP with 150 focused tests and independently
failing semantic/generation negative controls.

Task proof records retain their exact ancestor commits. In particular,
`5fc7a986` / `EVA2FEF71F` passed 1164 router/lifecycle tests, and
`2ce5fec8` / `EVFBC9D314` passed 392 controller/transport/workload tests.
Historical task branches may lag current main; the combined tested source,
not a claim that every old branch is current, is the acceptance basis.
CI must pass for the final PR head before merge.

Upstream PR #471 (`b85b5d27`) is retained. Its portable host services and
workload navigation coexist; a literal compatibility regression reconstructs
its MCP catalog digest after removing only this batch's intentional additions.

## Remaining work

All IDs below are in `fleet-node-enrollment` and remain unaccepted:

| Task | Remaining capability |
| --- | --- |
| T004 | Fixed receiver dispatcher and trusted target validation |
| T012 | Dedicated controller bootstrap staging endpoint |
| T013 | Constrained SSH recovery transport |
| T005 | Transactional activation and crash recovery |
| T014 | Mandatory installed controller build identity |
| T015 | Transport selection, exact acceptance and rollback orchestration |
| T006 | Fleet bootstrap CLI |
| T016 | Bootstrap controller-command/manifest parity |
| T007 | Complete enrollment documentation, threat tests and cross-platform release gates |

The next trusted-context composition is a design breadcrumb in
`.tickets/2026-09-05-bootstrap-trusted-context.md`; its proposed T004.10 is
not yet an imported State task or implementation.

A non-blocking pre-existing generic health HTTP-error detail/classification
issue is recorded in
`.tickets/2026-09-05-controller-health-http-error-boundary.md`.
It is distinct from the repaired successful-health identity envelope; do not
claim that every non-2xx health error is now input-free.

Managed controller publication recovery, exact installed version/identity
parity, rollback readiness and real Pi/OpenClaw client smokes are deferred
operator gates. See `.tickets/2026-09-05-managed-controller-recovery.md`.
No package, controller/router image, host service, model, route, private
configuration, operator ACL or active client profile was changed by this source
checkpoint. Source publication is not live deployment or model promotion.

## Resume and rollback

Preserve the isolated integration worktree and the dedicated delivery State
worktree. Its ignored runtime database and claim evidence are local coordination
data, not public repository artifacts. Resolve the public/private pair, read
the current PRD source/status, and choose a fresh scoped worktree before resuming.
The nine unfinished tasks and deferred tickets require a new execution decision.

If source rollback is needed, revert the merged source PR. This checkpoint
requires no live-service rollback because it does not deploy anything.
