# Design study validation

September 10, 2026 · Interactive prototype only

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
