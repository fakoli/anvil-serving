# Workbench continuation packet

Updated September 10, 2026 · Concept 03. Read this packet first after context
reset, then only the linked files needed for the next task.

## Current status

Revision 3 is implemented and validated as an interactive design concept.
Independent Sol/high final review approved its bounded scope with no remaining
material defects. Production integration has not begun. No serving process,
Connect implementation, private operator configuration or Anvil State was changed.

Work is saved on branch `codex/anvil-workbench-concept` in the existing isolated
worktree. Inspect status and the latest design commit before continuing. Revision
2 baseline was `92d92e1c`; revision 3 follows it. The unrelated pre-existing
untracked `.pi-subagents/` and `_pull_backup_20260807/` directories are untouched.

## Accepted direction

- Workbench combines experiments, deterministic run phases, compare and evidence.
  The cloud operator model remains independent from the model being benchmarked.
- Playground uses explicit connector, model/recipe and request preset selection.
- Models & recipes supports editing candidates and separately reviewing a load.
- Anvil work owns PRDs and task context. Pi is a compact chat panel inside a task's
  Agent session view, with folders, conversation threads and secondary menus.
- Observability is a workspace with the important Grafana dashboard groups.
- Compute is the last operational workspace: host inventory, served workloads,
  Docker/native identity, logs, configuration, reviewed start/stop and exec.
- Settings holds General, Connections, Pi environments, Access & sessions
  (Connect) and Data & evidence. Documentation remains a separate utility.
- Use the Serving docs navy/cyan/amber palette, one document scroll owner and
  small source-aware machine/operation indicators. Include a laptop compute role.
- Keep development token-efficient with bounded independent agents and durable
  checkpoints. Another process owns Connect; avoid competing implementation.

## Implemented behavior and limits

Six operational navigation destinations: Workbench, Playground, Models & recipes,
Anvil work, Observability, Compute. Settings and Documentation are utilities.
Legacy `#experiments`, `#sessions`, `#access` and `#system` links resolve to their
current destinations; sessions/access/system also replace the old URL fragment.

All data is synthetic and resets on reload. Pi/Pi Web are not imported or running.
Pi threads retain separate local drafts/transcripts; new threads start empty;
branches clear transient input/UI state. Model/thinking controls are locked while
a demo turn is busy. No model call, tool execution or real test result is produced.

Settings drafts survive section navigation; Save applies local preferences or
future-session defaults. Connect administration retains final-admin protection
and cascading browser-to-terminal revocation, simulated only.

Observability covers Fleet, Model performance, Physical GPUs, Host resources,
Benchmark history, Container logs and Monitoring health. Existing Grafana panel
categories informed the layout; no private queries, addresses or operator IDs
were copied. Dashboards state fleet versus selected-host scope. Missing/stale
telemetry is unknown; retained history remains separate. Benchmark windows are
per-run, not the illustrative 15m/1h chart window. Grafana context is a proposal,
not a configured link or embedded live dashboard.

Compute shares exact candidate/deployment objects with Models and Playground.
Reviewed Atlas stop/restart changes target availability. A saved Finch r2 candidate
starts as r2 and remains r2 in Playground. Orion remains placement-blocked because
its two-GPU requirement cannot bypass existing reservations. A stop does not
release reservations. Log filtering is literal; console drafts/output are keyed
by host and workload, with explicit non-execution text. No Docker/host command
is run by the prototype.

## Files and ownership

All changes belong to `docs/design/workbench/`:

- `index.html`, `workbench.js/css`: shell, shared fixture state and integration.
- `pi-surface.js/css`: isolated Pi chat and conversation state.
- `settings.js/css`: settings sections, drafts, preferences and task wrapper styles.
- `observability.js/css`: dashboard composition and synthetic chart/log fixtures.
- `compute-surface.js/css`: workload projection, reviewed lifecycle, scoped console.
- `DESIGN.md`: journeys, architecture alternatives, contracts and delivery slices.
- `PI-REUSE.md`: primary-source Pi reuse research and exact-revision spike criteria.
- `RUNNER-CONTRACT.md`: fake-runner/coordinator lifecycle acceptance matrix.
- `VALIDATION.md`: observed checks, review fixes and explicit verification limits.
- `README.md`: direct launch and suggested walkthroughs.
- `fonts/`: bundled Barlow / Barlow Condensed and OFL licenses.

Lead integrated Settings/Observability/shell and ran browser checks. Separate Terra
workers owned Pi and Compute; independent Sol reviewed their changes and root
integration. No competing component writers remain active.

## Validation and preview

Revision 3 checks passed: Settings draft/save/discard and environment propagation;
Connect last-admin/cascade; Pi thread/branch/new-conversation behavior and focus;
all dashboard scopes, stale/missing sources and time context; Compute shared
lifecycle/recipe identity, placement block, log filter, console isolation and
keyboard tabs/command palette. Desktop screenshots were inspected. At 320px the
new workspaces, all seven dashboards and Settings sections have no document-wide
horizontal overflow or nested vertical scrolling. Viewport overrides were reset.
Five JS modules passed syntax checks; frontend formatting and diff checks passed.
Final browser load reported no warnings/errors. These are prototype checks, not
live service acceptance or full accessibility compliance.

Open `index.html` directly without a server, build, credentials or network. A
local preview has used port 8765; verify it still exists before relying on it.
No production dashboard or model service needs restarting to view this concept.

## Next actions

1. Continue human design review of Concept 03; keep the final product name and
   packaging/repository extraction decision open until contracts settle.
2. Pin Pi Web/Pi and test the smallest integration seam in PI-REUSE.md with fake
   owner adapters. Prove embedding, task identity, extension UI and recovery;
   do not assume a third-party app can simply be iframed.
3. Exercise RUNNER-CONTRACT.md with a fake runner and State coordinator. Before
   implementation planning, inspect Anvil status against the exact checkout and
   reconcile historical workspace identity. Do not initialize competing state.
4. Coordinate the current Connect owner contract and build bounded vertical
   slices through supported APIs. Live exec, Grafana integration and workload
   operations require the production owner adapters described in DESIGN.md.

The earlier explicit design goal was completed before these feedback revisions.
Do not create duplicate goals or a production implementation task without a new
request. No implementation PRD was claimed, approved or mutated in this phase.
