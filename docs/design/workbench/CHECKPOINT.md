# Workbench continuation packet

Updated: September 10, 2026. Start here after a context reset; then read only the
linked documents relevant to the next action.

## Objective and scope

A goal in the current Codex thread tracks an implementation-ready design
for an Anvil workbench: model recipe/connector/preset playground, experiment
inspection/evidence, fleet HUD, exact-project PRDs/tasks and sandboxed Pi coding
sessions. Current phase is design and interactive prototype validation, not a
production migration. The design phase is complete and ready for human feedback;
production implementation has not begun. The full design is [DESIGN.md](DESIGN.md).

## Accepted user direction

- Experiments, tests, answering questions and doing project work are primary.
- Open WebUI means model selection, connectors, presets, playground and settings.
  A fork is allowed as an option; no architecture choice is accepted yet.
- Pi means the P-I coding agent, not merely a Python executor.
- Include fleet health and open PRDs associated with the exact selected project;
  explore executing approved work through the UI.
- Optimize token use and continuation across context windows. Use bounded
  sub-agents and durable workflow packets.
- Full access and necessary disruption are authorized. Another process is
  working on Anvil Connect; avoid competing changes.

## Decisions proposed, not yet accepted

- Working name: Anvil Workbench.
- Distinct application boundary, source initially beside existing facade;
  preserve backend ownership. Extract packaging/repository later if warranted.
- Evaluate unmodified Open WebUI integration before a permanent fork. The
  comparison and decision spike are in DESIGN.md.
- Pi behind a dedicated runner using its supported RPC/SDK, isolated worktrees,
  explicit model connection, resource limits, Anvil claims and evidence gates.

## Files and ownership

All edits are confined to `docs/design/workbench/`. The lead owns edits. A bounded
independent Sol/high review is read-only. Existing unrelated untracked directories
`.pi-subagents/` and `_pull_backup_20260807/` were present before this work and are
untouched. No production source, private configuration or Anvil State was changed.

- `index.html`, `workbench.css`, `workbench.js`: offline interactive concept.
- `fonts/`: bundled Barlow and Barlow Condensed plus OFL licenses.
- `DESIGN.md`: journeys, architecture options, backend gaps, delivery slices.
- `README.md`: launch and demo behavior.
- `CHECKPOINT.md`: this packet.
- `RUNNER-CONTRACT.md`: early fake-runner lifecycle spike and acceptance matrix.
- `VALIDATION.md`: observed browser/source checks and independent review outcome.

The design is saved on branch `codex/anvil-workbench-concept` in the existing
isolated worktree. Use the branch's latest design commit as the continuation
baseline; inspect local status before any further edits.

The exact-checkout Anvil status read resolved initialized historical state;
there were no active claims in that snapshot. No new state was initialized,
planned, claimed, approved or mutated. Recheck before implementation planning.

## Current implementation

Eight pages: Workbench, Experiments (four run sections), Playground, Models &
recipes, Anvil work, Pi sessions (activity/diff/tests), System & logs, Architecture.
Demo actions include bounded experiment review/submission, evidence attachment,
offline model rejection, preset updates, canned response, Pi plan review, log
filtering, stale HUD and Ctrl/Cmd+K search. All data is clearly synthetic.

No model calls, runtime changes, sandbox launch or production deployment occurred.
Opening `index.html` directly requires no server/build/network. A temporary local
design preview uses port 8765; if unavailable, open the file directly. Do not
assume an old preview server survives a context reset. The existing Observatory
was visually inspected using its isolated fixture, not a live deployment.

## Validation so far

- JavaScript syntax passed and frontend files formatted with Prettier 3.6.2.
- Browser: experiment edit → review → local simulated submission; comparison
  caveat; evidence attachment to sample task.
- Browser: preset temperature changed to 0.8; offline recipe rejected without
  substitution; canned response rendered; literal HTML payload created no image.
- Browser: Pi environment review → retained local plan → diff/tests panes.
- Browser: literal log filter; stale HUD; command palette filtering and Escape.
- Both 390px visual review and 320px main-page reflow checks passed. The narrow
  experiment modal and review controls were reachable.
- Final checks confirmed connector-aware unavailable targets, disabled Send,
  exact request capture and explicit unsent-versus-submitted demo provenance.
- Independent Sol/high review approved the design concept for the next contract
  spike; no material defects remained in its bounded scope. All findings and
  corrections are summarized in VALIDATION.md.

These are prototype checks, not acceptance of live services, real benchmarks,
real Pi execution, screen-reader usability or complete accessibility compliance.

## Next actions

1. Gather human feedback on the interactive design, navigation and app/fork choice.
2. Begin the early fake-runner contract spike described in RUNNER-CONTRACT.md.
   Test only fake adapters until the real State/Connect contracts are coordinated.
3. After design direction settles, create appropriately scoped Anvil planning
   records through its supported engine, coordinate Connect seams, and implement
   one vertical slice at a time. Do not treat this proposal as automatic approval
   of an Open WebUI fork, model promotion or production deployment.
