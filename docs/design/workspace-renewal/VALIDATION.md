# Acceptance and handoff contract

The checklist below defines acceptance for the original plan. Implementation
receipts live in the selected private operator repository; the task manifest
remains the original scope and dependency record.

Implementation acceptance scope (2026-09-20 owner decision): use disposable
canonical Anvil State, created and read through supported owner APIs, for the
successful persisted-plan and task-execution journeys. This must exercise real
State contracts, not mocked success responses. Recovery of existing live State
and deployment are separate work; neither is required to certify the source
implementation in this delivery. Retain those unperformed live checks explicitly
in the final receipt. This scope supersedes the configured-live prerequisite in
the original resume rules below, without weakening their independent review or
dependency requirements.

## Source acceptance (2026-09-20)

The implementation retains separate native host Pi and isolated task authority.
Native Pi Web is built from the pinned source and packaged bridge; task runs use
frozen roots, canonical claims, reviewed patches, and owner-submitted evidence.
The coordinated evidence path requires Anvil 0.6.11 / API 17.

Repeatable browser checks are under `tests/ui/workbench/`:

| Harness | Exercised contract |
|---|---|
| `state_browser.cjs` | Disposable canonical State through the production plan adapter, matching revision/digest, outline and reload |
| `workspace_browser.cjs` | Task selection, managed Pi links, lost-response reconciliation, frozen files and evidence controls |
| `host_browser.cjs` | Native thread metadata, context files, exact links, bounded history refresh, authority changes, responsive navigation |
| `pi_native_browser.cjs` | Actual pinned Pi UI: prompt/stream, steer, follow-up, stop, ten-image limit, desktop/mobile embedding and bridge rejection |
| `browser.cjs` | Workbench discovery, pagination, active history, retained evidence comparison, project defaults and keyboard navigation |

The native Pi browser gate uses a deterministic RPC peer and makes no provider
calls. Canonical State integration tests separately exercise real claims,
multiple writable roots, read-only context, evidence submission and recovery.
These source checks do not certify a live deployment, model quality, or repair
of an existing State history. Existing live State recovery, service rollout and
deployed client acceptance remain separate work by the owner's decision above.

| Journey | Required evidence |
|---|---|
| Open a persisted plan | Real supported CLI contract + safe Markdown render; revision/digest retained; divergent projection yields its safe typed code without repair |
| Open Playground | Pi tab is usable in-page; Model test history retained; telemetry outage does not block chat |
| Preserve the Pi experience | Same five T05 journeys in pinned Pi Web and Playground; retain familiar layout/controls, command behavior and keyboard/composer focus; record differences |
| Resume an existing thread | Same native session/owner/project/worktree; no duplicate writer; stream resumes after disconnect |
| Use multiple directories | Correct primary cwd; explicitly selected writable secondary plus read-only third root; per-root claims, patches and verification; partial acquisition/transfer and lost-lease recovery; negative write/escape tests; host mode discloses operator access |
| Inspect worktree | Correct repository/branch, staged and working diff, bounded files; selecting another thread cannot retarget active writes |
| Change Pi settings | Explicit scope and timing, server-confirmed result; unavailable settings explained; model/provider choice preserved |
| Handle extensions | Select/confirm/input/editor round-trip; unsupported UI explicit; reconnect cannot auto-approve or double-answer |
| Discover a run | UI-origin and owner/CLI-origin records appear automatically, with provenance/correlation and artifacts; backfill is idempotent |
| Keep activity current | Measured 10-second discovery/5-second active refresh budget while visible; hidden pause, immediate resume and partial-source recovery with one hung owner; request deadlines, page/concurrency limits and cancellation |
| Execute an Anvil task | Eligible disposable fixture starts exactly once; canonical claim/worktree, Playground thread and Workbench run correlate |
| Submit task evidence | Stop/capture/review/verify/submit retains exact digests and frozen commands; independent review remains separate |
| Deny unauthorized access | Another principal cannot list/read/control sessions, roots, task runs, artifacts or counts; revocation closes control paths |
| Migrate/rollback | Single-root config and retained histories/bookmarks survive; service pin and source build reproducible; repeat setup is no-op |
| Keyboard/mobile | Menu focus/escape, accessible pane controls, readable errors and reachable composer at narrow widths |

Prefer deterministic fake Pi/owner adapters for functional and negative tests.
A bounded real Pi smoke belongs only in an authorized acceptance phase. Use
existing independent tests/graders; the driving/evaluated model does not certify
its own task quality. Avoid live production benchmark runs just to create UI data.

<a id="resume-rules"></a>

## Resume rules and completion authority

`tasks.json` is the immutable proposed scope/dependency manifest. Its `planned`
fields must not be read as live execution state. After implementation is authorized,
use the selected **private operator repository**, relative directory
`plans/workspace-renewal/receipts/`, for durable evidence. Store one receipt as
`<slice-id>.md` (for example `T03a.md`; use `T01.md` for an unsplit task) and the T02
decision as `T02.integration-decision.json`. Preserve revisions through private
Git history. No receipts or gate passes exist yet from this planning session.

If registered in Anvil, record the canonical task/claim ID in each receipt and
attach its evidence reference using supported Anvil CLI/MCP. Anvil remains the
status authority; a receipt cannot override an open review gate or blocked task.
Before registration, independently reviewed receipts alone track this local plan;
they do not create canonical tasks or permission to execute them.

For every authorized run:

1. Inspect the current checkout and any canonical task state. Read predecessor
   receipts, not previous chat summaries. Missing or unreadable evidence means
   the prerequisite has not passed.
2. Select the first unfinished slice whose base task dependencies, its own
   `depends_on_tasks`, and earlier `depends_on_slices` are verified. For example,
   T05a needs T02/T04 but not T03; T05b/c also need T03. Split packets finish only
   when **all** slices and packet acceptance
   pass. An unsplit packet is one slice. Independent packets may proceed while
   another is blocked; T01 remains blocked if the real plan cannot be read, so
   T07 cannot be called ready on the strength of a mocked/error-only repair.
3. An author records `partial` (unfinished), `blocked` (named external/design gate),
   or `implemented` (author checks complete). An independent reviewer records
   `verified` only after checking the exact source revision, acceptance evidence,
   applicable dependency receipts and unresolved findings. No self-certification.
4. T03/T05 additionally require `T02.integration-decision.json` with `status: passed`,
   selected transport, immutable tested pins and positive/negative evidence,
   referenced by a **verified T02 receipt**. An author-written `passed` alone does
   not open the gate. Record the decision file's digest in that reviewed receipt.
5. A resume continues the last partial/blocked slice from its next concrete action;
   it does not restart passed work or skip a blocker. Check whether a blocker was
   actually resolved. Changed relevant source, pins, contracts or dependencies
   require renewed affected checks/review before using the old verification.
   An `implemented` slice waits for independent review; resume the review or address
   its findings instead of rebuilding it or advancing to its dependent slice.

Cross-turn acceptance check: a fresh agent must pick T03a with verified T02 and a
matching passed decision; it must refuse when the decision is blocked, missing,
changed since review, or merely author-asserted. With verified T03a and partial
T03b it resumes T03b, never T03c. T08 with live checks pending stays `blocked` or
`implemented` for its local work, never fully `verified`.
With T02/T04 verified and T03 blocked, T05a is eligible while T05b/c are not.

## Receipt format

Each packet's reviewer should receive a short Markdown receipt:

```
Packet/slice: Txx / Txxa (or unsplit)
Canonical task/claim: exact IDs, or not registered
Status: partial | blocked | implemented | verified (rules above)
Source: commit and changed files
Inputs: dependency receipt revisions; decision/config/package digests
Contracts: schema/API/ownership decisions and migrations
Checks: exact commands, counts, failures and artifact references
Browser: measured journeys, versions, dimensions, failures
Security: owner/authorization/negative test results where applicable
Deployment: not attempted | pending authorization | measured result
Rollback: preserved data and reversal procedure
Remaining: concrete blockers, limitations and next ready packet
Continuation: next exact action, touched files, checks still needed; no hidden chat context
Independent review: reviewer identity, reviewed commit, scope, findings and dispositions
```

Keep public receipts sanitized. Runtime transcripts, personal paths, domains,
identities, tokens, raw logs and database backups do not belong in this folder.
No task completion is implied by an empty receipt template or checked source-text
assertion. Release checks follow repository instructions and the
`anvil-serving-release-readiness` skill when the scope reaches delivery.
