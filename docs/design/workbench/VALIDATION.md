# Design study validation

September 10, 2026 · Interactive prototype only

## Revision 3 observations

Settings, task-attached Pi, Observability and Compute were reviewed in the same
isolated design preview. All records and actions remain synthetic; production
Grafana, Pi, Docker, Connect and Anvil State were not exercised.

| Revised journey | Observed result |
| --- | --- |
| Navigation | Connect access appears under Settings; Pi under Anvil work → T-04 → Agent session; Observability and Compute are separate workspaces |
| Settings drafts | Workspace name draft survives section navigation without changing the shell until Save; saved name, compact density and HUD preference apply |
| Settings environment | Saved 3 CPU / 6 GiB / 45 minute defaults appear in the next task environment review |
| Settings access | Last-admin disable rejected; browser-session revocation cascades to its terminal session |
| Settings corrections | Discard restores General heading focus and saved value; its notice does not leak into Pi environment settings |
| Pi threads | Thread selection restores its own draft; new branch clears draft; new conversation starts with zero messages and blank composer |
| Pi controls | Whitespace or keyboard deletion disables Send; active turns disable model/thinking; finish restores composer focus; branch/new conversation focus their title |
| Pi interaction fix | Replacing animation-frame focus scheduling and restricting change handling to selects removed lost focus during composer actions |
| Observability scope | Fleet is fleet-wide; MacBook model view reports no Atlas sample; Apple GPU view uses unified memory with unsupported temperature/power unknown |
| Stale and missing sources | Current throughput becomes unknown while current demo deployment count and historical charts stay separate; Harness host/log data is unavailable |
| Dashboard time | One-hour fixture changes chart samples; benchmark history instead shows All retained run windows, including in Grafana context |
| Log filtering | Literal WARN query retains matching sample line and input caret/focus |
| Shared deployment state | Atlas stop disables its Playground target; reviewed restart restores atlas-fp8-r3 |
| Candidate identity | Finch context 16384 saved as r2; Compute review, active configuration and Playground all retain finch-mlx-r2 |
| Placement safeguard | Orion Start shows owner-placement requirement, keeps reservations, and offers no confirmation bypass |
| Compute selection and logs | External HUD location change selects matching Finch row/detail; literal filter preserves caret and returns only readiness record |
| Compute console | Draft/output remain separate for Atlas and Media; typing does not submit; submission explicitly says command not executed |
| Keyboard | Compute Home selects/focuses Logs; Ctrl+K from the console opens the global palette and close returns focus to its input |
| Narrow layouts | At 320px, all seven dashboard groups, Compute workload/configuration, Pi chat and five Settings sections have no document-wide horizontal overflow or nested vertical scrolling |

Desktop screenshots of Observability, Compute and task chat were inspected.
Viewport overrides were reset. These checks do not establish full accessibility
conformance or any live service acceptance. The browser automation's empty-string
fill did not dispatch the expected input change; the user keyboard path (select
all then Backspace) and whitespace path were checked explicitly instead.

Independent Sol review identified Settings route/notices/focus issues, Pi
thread/transient/focus issues, Compute input/table/tab/isolation issues and the
dashboard-window / global-shortcut issues. Corrections were made in the bounded
components. Final lifecycle identity checks passed. Independent Sol/high final disposition: approved for the bounded Concept 03 revision, with no remaining material findings. All five JavaScript modules passed syntax checks; frontend formatting and diff checks passed. The final browser load had no warnings or errors.

## Revision 2 observations

The same isolated preview was revised after user feedback. No production sources,
Connect workstream, model owner or operator configuration was changed.

| Revised journey | Observed result |
| --- | --- |
| Palette and desktop layout | Serving docs navy/cyan/amber tokens applied; desktop screenshot inspected; right HUD removed |
| Single scroll owner | No independently scrolling vertical elements across eight main pages at 320px; no document-wide horizontal overflow |
| Dense tables | Connect tables retain bounded horizontal scrolling; document width stays within viewport |
| Deterministic run flow | Review → simulated submission → advance → pause/resume changes phase and runner indicator; Advance disabled while paused |
| Run identity | New flow identifies EXP-043; retained overview/comparison/evidence identify historical EXP-042 |
| Cancel review | Reviewing and cancelling a second experiment does not rewrite the submitted run |
| Pi conversation | One submitted turn, steering note and manual finish produce the expected messages and Working/Awaiting review state |
| Pi text safety | Literal image/event-handler markup creates no image node |
| Pi session isolation | Branch clears transient draft/diff; original and branch restore separate draft and thinking selections; original diff preserved |
| Pi extension | Confirmation records a local review note; no tool grant or execution occurs |
| Recipe editor | Finch context 8192 → 16384 saved as r2; save has no load effect; separate review simulates load |
| Exact recipe identity | r1 remains marked not loaded, active revision shows r2, saved candidate shows loaded/unqualified |
| Compute selection | MacBook target resolves to the loaded Finch r2; mismatched compute disables Send without substitution |
| Request capture | A last submitted request from another target is rejected when converting to a new test |
| Connect last admin | Disabling the final administrator is rejected beside the form |
| Connect grants | Researcher access can be reviewed and disabled while retaining identity |
| Connect cascade | Browser revocation preview names its terminal child; confirming marks both revoked |
| System context | MacBook logs and Grafana preview show MacBook and the exact loaded Finch revision |
| Documentation | Anvil State summary and source link render in the reader; topic selection retains its named content relationship |
| Dialog focus | Recipe edit → review focuses the new heading; 320px modal measures 292px and controls remain reachable |
| Workbench controls | Named tablist and labeled tabpanel expose current section; ArrowRight and Home move focus/selection correctly; source also handles ArrowLeft/End |

Keyboard focus and DOM relationships were checked; these are not screen-reader
or comprehensive keyboard-conformance results. The narrow viewport override was
reset. Earlier revision observations below are retained as historical checks,
including the former Pi evidence-tab layout, which revision 2 replaces.

Independent Sol/high revision review found exact-recipe labeling, selected-host
drill-down, Pi session state, dialog focus and section-semantics issues. All five
were corrected and the affected browser paths rechecked. Final focused reviewer disposition: **approved for the bounded concept revision**;
no material defects remained in the five rechecked areas. Both JavaScript files
passed syntax checks. Final reload and Access/Workbench navigation produced no
new browser warnings or errors. The browser log retains earlier partial-edit
errors from before the Access function was added; these are not final-load errors.

## Scope

The existing Observatory was inspected through its isolated synthetic fixture.
The new concept was served locally with no backend connection. No live model,
benchmark, Pi process, container lifecycle, project-state mutation or production
authentication flow was exercised.

## Browser observations

| Journey | Observed result |
| --- | --- |
| Experiment configuration | Edited name and concurrency appear in review; simulated submission adds the local record |
| Comparison | Incompatible and incomplete runs carry an explicit comparability warning |
| Evidence attachment | Local demo link appears on the chosen sample Anvil task |
| Preset selection | Creative sets temperature to 0.8 |
| Offline/disconnected selection | Exact served target becomes unavailable; Send disabled; no substitution |
| Playground response | Fixed response visibly labeled canned; no model called |
| Literal text | An HTML image/event-handler string appears as text; no image node created |
| Session → test | Review contains exact connector, recipe, target, alias, preset, temperature, output limit, prompt, history and tool/schema state |
| Request provenance | Unsent drafts and submitted demo requests are distinguished; target display and snapshot use the same validated catalog |
| Pi plan | Environment, resource/context constraints and independent review boundary visible; local plan does not start a runner |
| Pi evidence tabs | Sample diff and test output visible with synthetic-result labels |
| Logs | WARN filter returns only the matching sample line |
| Stale telemetry | Metric values become unknown; independently sourced owner/task snapshots retain source timestamps |
| Command palette | Ctrl+K opens, filtering locates Pi, Escape closes |
| Narrow layout | At 320px, Workbench, Playground, Models, Anvil work, Pi sessions and System have no document-wide horizontal overflow |
| Narrow review | Experiment modal width 292px within 320px viewport; Review and Simulate controls reachable |
| Mobile visual | 390px screenshot inspected; chart type enlarged for the narrow layout |
| Browser console | No warnings/errors in the final checked preview |

The temporary viewport override was reset. The user-facing preview tab is
retained. The old Observatory fixture process was stopped after inspection.

## Source checks

- `node --check docs/design/workbench/workbench.js` passed.
- Prettier 3.6.2 check passed for HTML, CSS and JavaScript.
- `git diff --check` passed for the tracked diff. New files were also inspected
  through formatting checks; no production sources were edited.

## Independent review

A separate Sol/high reviewer examined ownership, workflow fidelity and design
gaps without duplicating browser tests. The review led to:

- One Anvil State task coordinator for claim/evidence mutations; Connect retains
  its actual transport/identity role. An initial suggestion to make Connect the
  State authority was withdrawn after checking the current product contracts.
- Explicit canonical benchmark/artifact ownership under Evaluation & Evidence,
  and task evidence-link authority under Anvil State.
- Connector-aware exact target display and immediate unavailable-target state.
- Captured playground request snapshots in experiment review.
- Independent source timestamps in the HUD and stale metric treatment.
- An early fake-runner lifecycle contract and acceptance matrix before broad Pi
  implementation.
- Explicit lease suspension, cleanup recovery and verified closure states, with
  execution and Anvil acceptance outcomes kept separate.

Final reviewer disposition: approve the design concept for the next contract-spike
phase. No material defects remained in the bounded reviewed scope. This is design
review approval, not authorization or qualification of a production rollout.

This record does not claim live qualification, full test-suite execution,
screen-reader testing, forced-colors testing, 200% native zoom testing, benchmark
correctness, sandbox isolation or actual Pi/State integration. Those belong to
the implementation acceptance gates described in DESIGN.md and RUNNER-CONTRACT.md.
