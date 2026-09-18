# Pi workspace, Workbench and Anvil Work renewal

Status: proposed implementation plan, not an approved PRD or deployment authorization.
Observed: 2026-09-18. Product source inspected: `3048abeb2a066c159760e0e9ea168b801a078b84`.

Planned product label: **Pi** replaces **Playground** in navigation. Existing
`#/playground` URLs and source filenames remain compatibility anchors. References
to Playground throughout this packet mean that same surface, not another app.

## Start here — execution agent

The owner requested **planning only** in this session. Implement only after a
subsequent implementation instruction. This packet is intended for a bounded
coding agent such as GLM 5.3 Flash; do not attempt the entire redesign in one turn.

1. Read this file and [the design and findings](PLAN.md). Then use the
   [engineering guide](IMPLEMENTATION-GUIDE.md) and [verified source map](REFERENCES.md)
   for the selected task; do not load every packet into the agent context.
2. Resolve the current public/private workspace using the installed resolver;
   read the checkout's AGENTS.md, README.md and CLAUDE.md. Preserve dirty work.
3. Inspect Anvil project status using the exact checkout. This packet does not
   create, approve, or claim Anvil tasks. If requested to register the work,
   use supported Anvil planning/claim tools, never direct state edits.
4. Read `tasks.json` and the [resume rules](VALIDATION.md#resume-rules); take one
   dependency-ready slice whose gates have reviewed receipts. The task IDs below
   are local packet IDs, not canonical Anvil task IDs. `planned` records the design
   baseline, not current execution state.
5. Read the relevant source files fully, trace callers, implement the smallest
   change, and run the packet's checks. Prefer existing helpers and tests.
6. Obtain independent review at authority boundaries. Record delivery using
   [the validation contract](VALIDATION.md), including failures and remaining gates.
7. Stop after one slice (or an unsplit packet). Save its receipt at the defined
   private location, including continuation details. Never describe planned
   acceptance as completed acceptance.

## Engineer starter kit

- [Clickable mockups and screenshot guide](mockups/GUIDE.md): Pi, Workbench and
  Anvil Work, with synthetic interactions and explicit implementation limits.
- [UX corrections and Open WebUI inspiration](mockups/UX-NOTES.md): current
  improvements mapped to packets, plus separately gated follow-up capabilities.

- [Implementation guide](IMPLEMENTATION-GUIDE.md): current call chains, patch-shaped
  sketches, proposed interfaces, migration order and first regression to write.
- [Source map](REFERENCES.md): verified files, symbols, line anchors and reuse tests.
- [Example contracts](examples/contracts.json): current task-start body and clearly
  labeled proposed project, thread, run, settings and decision records.
- [Runnable examples](examples/reference_examples.py): three pure patterns with
  self-checks; no live calls, writes or installation steps.
- [Primary external references](UPSTREAM-REFERENCES.md): Pi Web, official Pi RPC,
  browser framing rules and accessible dialog behavior, with compatibility limits.

Examples are starting points, not production code. `tasks.json` points each task
at just its relevant guide section, source map, examples and external references.

## Delivery order

| Packet | Outcome | Depends on |
|---|---|---|
| T01 | Explain plan-read failures; diagnose State projection safely | — |
| T02 | Prove Pi Web embedding and identity/runner boundary | — |
| T03 | Define multi-directory projects and scoped explorer access | T02 |
| T04 | Collapsible application navigation and resilient page shell | — |
| T05 | Pi home, threads, files, worktrees and settings | T02, T04; T03 additionally for T05b/c |
| T06 | Discover runs from their owners and keep Workbench current | — |
| T07 | Streamline Anvil Work and launch linked Pi task runs | T01, T03, T05, T06 |
| T08 | Migration, independent acceptance and staged rollout | T01–T07 |

Recommended sequence: T01, T02, T04, T05a, T03a–g, T05b–c, T06a–c, T07, T08. Separate agents
may work on independent packets when the execution scope permits delegation;
shared files need one writer. The critical design gate is T02: validate reuse before building
another Pi frontend. T01 and T06 deliver useful fixes independently of that gate.

## Boundaries

Owner-confirmed choices: retain Pi Web's familiar look and interactions inside
Playground; existing Pi conversations open first; secondary project
directories can be explicitly writable. T03 includes the required canonical
claim/evidence extension for isolated tasks rather than treating it as a UI toggle.

- Playground is the chat/project workspace. Workbench is the run/evidence hub.
  Anvil Work is the plan/task/execution/evidence workflow.
- Preserve Pi Web's existing sessions and the current managed task runner.
- Keep real origins, identities, roots, artifacts and deployment receipts private.
  Public examples use synthetic identities and protected secret references.
- No production model requests, service restarts, provider changes, live State
  repair, permission expansion or deployment occurred during this planning work.
- Existing design documents are historical context; this packet changes no
  current contract until its relevant implementation passes review.

See [REVIEW.md](REVIEW.md) for the independent plan review and incorporated fixes.

## Suggested execution prompt

> Read `docs/design/workspace-renewal/README.md`, `PLAN.md`, `tasks.json` and
> `REVIEW.md`, then the selected task’s guide section and source map. I authorize
> implementation of the next dependency-ready slice only, following VALIDATION.md's
> resume rules. Stop after that slice. Inspect current checkout and Anvil status before editing; preserve dirty
> work. Use the packet's source anchors and acceptance criteria, reuse existing
> owners and helpers, and add focused behavioral checks. Keep deployment, live
> State recovery and broader access changes pending their own authorization.
> Save a receipt following `VALIDATION.md`, then identify the next eligible slice.
> Do not implement the entire roadmap in one turn or declare mocked checks to be
> live acceptance.

This quoted prompt is a template for the owner to send later, not an instruction
or authorization to execute during the current planning session.
