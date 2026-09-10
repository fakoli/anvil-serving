# Workbench continuation packet

Updated: September 10, 2026. Start here after a context reset; then read only the
linked documents relevant to the next action.

## Revision 2 status

Human feedback accepted September 10 and implemented in the interactive concept:
Serving docs navy/cyan/amber palette; one document scroll owner; compact operation
indicators; experiment tabs inside Workbench; compute/model selection including
synthetic MacBook; recipe candidate edit/save/load review; Connect grants and
session revocation; documentation reader; conversational Pi with model/thinking,
branch/resume, steering, extension note and diff controls. Deterministic evaluation
is separate from the cloud operator model.

Research recommends a bounded Pi Web reuse spike, not an Open WebUI fork yet.
See PI-REUSE.md for primary sources, exact-revision requirements and integration
unknowns. The prototype has no imported Pi or Pi Web dependency.

Lead owns existing files. Terra delivered only pi-surface.js/css; Sol supplied
read-only independent review. Five material review findings were fixed and their
browser paths rechecked: recipe identity, compute drill-down, complete Pi session
state, dialog focus and section semantics. Final focused follow-up approved the bounded concept revision; no material
findings remained. Final reload and Access/Workbench navigation had no new
browser warnings or errors.
Production integration and the concurrent Connect work remain untouched.

## Objective and scope

A goal in the current Codex thread tracks an implementation-ready design
for an Anvil workbench: model recipe/connector/preset playground, experiment
inspection/evidence, fleet HUD, exact-project PRDs/tasks and sandboxed Pi coding
sessions. Current phase is design and interactive prototype validation, not a
production migration. The revised design is ready for further human feedback;
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
- Test Pi Web reuse for the conversational workspace before a custom Pi UI or
  permanent Open WebUI fork. Evidence and spike criteria are in PI-REUSE.md.
- Pi behind a dedicated runner using its supported RPC/SDK, isolated worktrees,
  explicit model connection, resource limits, Anvil claims and evidence gates.

## Files and ownership

All edits are confined to `docs/design/workbench/`. The lead owns edits. A bounded
independent Sol/high review is read-only. Existing unrelated untracked directories
`.pi-subagents/` and `_pull_backup_20260807/` were present before this work and are
untouched. No production source, private configuration or Anvil State was changed.

- `index.html`, `workbench.css`, `workbench.js`: offline interactive shell and flows.
- `pi-surface.js`, `pi-surface.css`: isolated simulated Pi conversation component.
- `PI-REUSE.md`: source-backed reuse recommendation and remaining contracts.
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

Eight primary destinations: Workbench (overview, run flow, compare, evidence,
events, all runs), Playground, Models & recipes, Anvil work, Pi workspace,
System & logs, Connect access and Documentation. Architecture remains a secondary
link. Old #experiments bookmarks resolve into Workbench.

All data is synthetic and resets on reload. Compute/model selects scope new work
and instruments; retained runs keep their original identity. Recipe candidates
and deployed revisions stay separate. Pi owns in-memory session/branch state;
its cloud operator selection is independent of the benchmark target.

No model calls, runtime changes, sandbox launch or production deployment occurred.
Opening `index.html` directly requires no server/build/network. A temporary local
design preview uses port 8765; if unavailable, open the file directly. Do not
assume an old preview server survives a context reset. The existing Observatory
was visually inspected using its isolated fixture, not a live deployment.

## Validation so far

Revision 2 browser checks and independent disposition are recorded in
[VALIDATION.md](VALIDATION.md). The list below retains the initial concept gates.

- JavaScript syntax passed and frontend files formatted with Prettier 3.6.2.
- Browser: experiment edit → review → local simulated submission; comparison
  caveat; evidence attachment to sample task.
- Browser: preset temperature changed to 0.8; offline recipe rejected without
  substitution; canned response rendered; literal HTML payload created no image.
- Revision 1 browser: Pi environment review → retained local plan → diff/tests panes;
  superseded by the revision 2 conversation and session-state checks.
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

1. Obtain feedback on revision 2, especially Pi reuse and the integrated run flow.
2. Pin Pi Web/Pi and test the smallest integration seam in PI-REUSE.md, using fake
   owner adapters first. Prove embedding, extension dialogs, identity and recovery;
   do not assume the third-party app can simply be iframed.
3. Exercise the early fake-runner contract in RUNNER-CONTRACT.md. Coordinate the
   current Connect contract before production adapter work.
4. Once direction settles, inspect exact-project Anvil status again, plan bounded
   implementation records through State and build one vertical slice at a time.
   The concept is not acceptance of live integration or automatic promotion.
