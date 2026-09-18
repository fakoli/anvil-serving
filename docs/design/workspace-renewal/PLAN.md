# Product design and investigation

## 1. What was established

Chrome inspection covered the deployed Playground, Workbench, Anvil Work and
Pi Web. Source inspection covered the current checkout and installed Pi Web
0.9.0. No messages were sent, runs started, settings saved or claims acquired.
The browser's deployed build fingerprint differs in form from the source Git
revision; source/deployment byte parity was not established. Findings below
separate browser observations, code evidence and unresolved causes.

| Finding | Evidence | Implication |
|---|---|---|
| Playground is a direct model conversation view | `static/views/playground.js` uses `catalog`, `conversations`, `messages`; Chrome shows connector/model/preset fields and basic history | Preserve it as a secondary “Model test” tab; make Pi the default Playground tab |
| Pi Web already exposes most requested features | Chrome shows project sessions, running state, explorer, changed files, worktree switcher, file/diff panel, model controls and Settings sections for General, Models, Skills, Sub-agents and Plugins | Reuse this service/UI before considering a replacement |
| Existing task Pi is a separate session population | `workbench_app/service.py::_pi_read` requires both project and task; `PiTaskBinding` includes principal, task and lease | Do not pretend host Pi Web sessions and isolated Anvil task sessions are interchangeable |
| Project configuration supports one checkout | `workbench_app/config.py` permits `checkout`, `anvil_binary`, optional `runner_root`; `Projects.public_project` exposes only IDs/label | Multiple directories need a declared project/root model and owner enforcement |
| Anvil Work “Read plan” fails | Reproduced on the deployed page; configured CLI reproduces `projection_not_converged` using `prd show` against the declared checkout | Diagnose State convergence; a Markdown renderer rewrite cannot fix this |
| Error detail is lost | `projects.py::run_bounded` rejects any nonzero exit before `Projects.cli` can interpret the JSON error | Preserve bounded typed error codes safely, not arbitrary stderr |
| Workbench is not a complete history | `static/views/workbench.js` reads only `operations`; `dashboard/console.py` lists its intent store, capped at 100 | External benchmark/Pi runs are not automatically discovered by this path |
| Workbench is explicitly excluded from shell auto-refresh | `static/observatory.js::refresh` and `scheduleResume`; Chrome shows eight retained operation rows dated earlier than this review | Incremental refresh and broader owner discovery are separate tasks |
| Some evidence is configured manually | `dashboard/console.py` merges configured evidence with operation evidence | A configured card is an imported/historical record, not proof of live ingestion |
| Page rendering depends on unrelated fleet reads | `static/observatory.js::refresh` treats fleet failure as page failure before chat/project rendering | Telemetry failure should not prevent opening a chat or reading a plan |

Source prefixes above: `anvil_serving/observability/dashboard/` for `static/`;
`anvil_serving/` for `workbench_app/`. Additional anchors are
`docs/design/workbench/PI-REUSE.md`, `workbench_app/pi_web.py`,
`workbench_app/pi_sessions.py`, `pi_rpc.py`, `pi_runner.py`, `task_artifacts.py`,
`benchmarking/jobs.py`, and `observability/fleet_workload_sources.py`.

The plan-read CLI supports `prd show`, `--json` and `--limit`; this reproduction
is **not** evidence of a missing command. Its installed version was
`0.6.7+53.gb1ad61cb35b5` (schema 21). The reason projection convergence failed
has not been established. Check the exact source/installed versions, workspace
identity and supported read-only State diagnostics before proposing recovery.
Never repair this by weakening the consistent-read guard or reading raw PRD files
as if they were canonical persisted State.

The particular newer runs the owner expects have not been reconciled against
all underlying stores. T06 must identify representative missing run IDs and their
owners before declaring complete coverage. No arbitrary filesystem crawler is
implied by “all runs.”

## 2. Intended experience

### Shared shell

A compact header contains a hamburger menu, current section, project selector
where relevant, and account status. The menu reveals Workbench, Pi,
Anvil Work, Models & recipes, Observability, Compute, Documentation and Settings.
It can be pinned on wide screens but starts collapsed for the chat workspace.
The **project/thread rail is separate** from this global menu and remains useful
when the global menu is hidden. Keep existing links/bookmarks working.

Show a compact connection/running indicator in chat. Expand compute telemetry on
demand; keep rich instruments in Workbench/Observability. Do not rebuild the
whole page to refresh the HUD. Use independent scroll areas for conversation,
thread list and file pane; pin the composer in the visible chat area.

Keyboard: named menu button with expanded state, Escape closes, focus returns to
its trigger; modal mobile drawer traps focus and makes background inert. Support
keyboard resizing or simple collapse buttons for panes, visible focus, reduced
motion and a narrow viewport without horizontal page overflow.

### Pi (formerly Playground): the harness workspace

Use **Pi** as the navigation label and page title. Preserve `#/playground` and
existing Model test conversation links; a route migration is unnecessary for a
label change. Source references to Playground below identify the existing module.

**Interaction target confirmed by the owner:** Playground should look and feel
like using Pi directly, following the deployed Pi Web experience. Reuse its
composer, transcript/tool presentation, session controls, supported shortcuts
and extension interactions. The outer shell adds application navigation and
project/task context; avoid duplicate chat toolbars or a new dashboard-style chat.
The diagram below allocates space, not a replacement visual design. Prefer
retaining pinned Pi Web UI; a proven native-RPC alternative must meet the same
journeys and explain unavoidable differences. Preserve supported Pi commands;
never turn arbitrary composer input into a new server shell endpoint.

```
[menu] Pi  [Project v]  [Conversations | Model test]   [connection]
[Threads / search] [thread title · worktree · model · thinking]
[new thread]      [conversation and structured tool events] [Files | Changes]
[recent threads]  [pending extension question, if any]      [preview / diff]
                  [composer · attachments · send/stop]      [project settings]
```

Keep the existing Model test conversations and identifiers intact. Do not
translate them into Pi sessions or mix their model controls with serving routes.

Owner-confirmed product choices: open Playground on **existing Pi conversations**
by default, with Anvil task conversations alongside them; allow explicitly selected
secondary directories to be writable. Project selection is not automatic permission
to edit every directory. These are design requirements, not live access changes.

Host Pi acceptance includes list/search/reopen/rename/archive of authorized threads;
new thread; streaming Markdown/code and tool activity; stop, steer and queued
follow-up; reconnect without duplicate sends; branch/resume; model/thinking
selection; context/usage status; attachment behavior; installed extension
select/confirm/input/editor requests and text widgets. Verify feature support
against the installed pin, not a moving upstream branch. Unsupported terminal
custom UI must be explicit and never auto-approved.
Managed task capabilities have a separate required/deferred matrix in
[T05](tasks/T05.md); host UI support is not evidence of managed RPC support.

A settings drawer separates current-thread settings, project defaults and
Pi-service/global configuration. Record each setting's owner, allowed values,
persistence and application time (next turn/new session/restart). At minimum,
expose model/thinking and supported conversation defaults; surface existing
General/Models/Skills/Sub-agents/Plugins through the reuse path where authorized.
Secrets, provider credentials and service/network authority remain protected.
Preserve provider choices; selecting a local evaluation target does not change
the chat provider. Do not silently overwrite router-derived model limits.

### Workbench: current activity and retained evidence

Default to Active runs and Recent runs, with filters for project, source/kind,
state, model, host and time. Each row opens one stable run detail: summary,
configuration, timeline, artifacts, verification and related task/thread.
Keep existing experiment launch flows reachable without six competing default
navigation choices. Show source coverage and last successful refresh beside
results. Distinguish “no matching runs” from “source unavailable.”

Refresh active status while visible, pause when hidden, refresh immediately on
return, and back off on failure. Start with bounded polling and cursor pagination;
reuse an owner event feed only if one is already suitable. A proposed acceptance
budget is new-run discovery within 10 seconds and active state updates within
5 seconds while visible. Do not rerender drafts, move selection, or reset scroll.

“Completed,” “passed verification,” “accepted by Anvil,” and “promoted” are
separate facts. A completed Pi chat is not a benchmark pass. Comparisons must
identify incompatible model/hardware/context/quantization/suite dimensions.

### Anvil Work: plan → task → run → evidence

Use a searchable plan/task rail, central plan or task detail, and contextual
execution/evidence drawer. A readable persisted plan shows title, revision,
digest, outline and linked tasks. Task rows show readiness blockers and claims.
Selecting a plan filters its tasks and survives refresh/deep links.

“Run with Pi” previews the task packet, effective provider/model, primary
checkout/worktree, accessible roots and permissions. Starting reuses the existing
claim → isolated workspace → Pi session flow and opens that exact thread in
Playground, retaining a return link to the task. Existing active runs show Resume
or Open instead of creating duplicates. If blocked, explain the exact condition.
Do not auto-approve draft plans or set tasks ready to make the button work.

Stop the runner before patch capture/transfer. Preserve independent verification,
reviewed patch digests, evidence submission and separate Anvil acceptance. Provide
recoverable states for claim/start uncertainty, lease expiry and interrupted
verification. Do not auto-merge code from a chat response.

## 3. Integration decisions and gates

### Reuse Pi Web; do not assume embedding is solved

Preferred first experiment: retain the separately owned, pinned Pi Web service
and mount its UI in the Playground Pi tab using an explicitly authorized origin.
Cross-origin framing preserves application isolation if authentication and
browser policy work. The current shell CSP inherits `frame-src` from
`default-src 'self'`, so an external frame will need a narrowly scoped change.
`frame-ancestors 'none'` controls who can embed the shell; it is not the setting
that grants the shell permission to embed Pi. Inspect child and edge headers too.

Test assets, cookies, login redirects, SSE/WebSocket transport, service worker
scope, navigation, browser history, sizing, keyboard and session expiry. Pi Web's
installed Next config declares root-scoped service-worker behavior; a subpath
reverse proxy is not automatically safe. Do not route its root `/api` or assets
over Workbench routes. Do not remove origin checks, authentication or CSP globally.

T02 ends with a recorded decision: separate-origin embed, proven subpath build,
or a narrowly scoped native RPC adapter if reuse cannot meet the contract. A
link that opens Pi Web elsewhere is a temporary fallback and **does not satisfy**
the requested in-Playground experience. Do not build multiple integrations in
parallel or silently turn a failed spike into a full frontend rewrite.

Critical boundary: current host Pi Web runs as an operator with existing local
sessions; managed task Pi runs in isolation under an Anvil lease. Merely framing
host Pi Web inside Workbench must not grant every Workbench user access to the
operator account. Define which principals may use that owner, how revocation
works, and how session ownership is enforced. Prefer existing service identity
rules; if multi-user scope cannot be proven, keep the host integration explicitly
owner-only and mark other accounts unavailable. Do not downgrade task isolation.

The host-session path may reuse Pi Web unchanged. Task execution continues through
its managed runner and existing RPC UI until Pi Web can prove support for that
runner binding. A common Playground frame can present both, with visible source
and consistent navigation, without copying session histories between owners.
Avoid new session databases: persist references/correlation and project metadata,
not another authoritative transcript. Never attach a second writer to an active
session owned by a CLI or another browser.

### Required decision receipt before dependent implementation

T02 is passed only with a selected transport, proven authentication/ownership
contract and explicit host-versus-task execution mode. A documented reuse failure
is a blocked decision, not a successful prerequisite. A native-RPC alternative
must prove the same bounded contract before T03/T05 may proceed.
Persist and independently verify this decision using the exact receipt and resume
rules in [VALIDATION.md](VALIDATION.md#resume-rules). Test in-Playground behavior
through the selected transport; frame-specific tests apply only to frame options.

Host Pi Web's operator process may access paths outside the selected project
through its tools. An explorer root filter is not a sandbox. Unless tool-level
confinement is demonstrated, label this mode **Owner host session — tools use
operator account access**; its roots describe context/navigation, not security
grants. Restrict it to the existing authorized owner and do not advertise per-root
write denial. The scoped mode must enforce every advertised root permission at
tool execution and remain the only mode used for managed Anvil task runs.
Choosing a project never widens an account's access or converts a host session
into an isolated task session.

Existing sessions without trusted project bindings appear in an **Unassigned
(owner only)** group. Preserve their native cwd/history and offer explicit
association with a declared project after verifying the owner, canonical root and
repository/worktree identity. A display association does not change cwd, tool
permissions or claim ownership. Never infer authority from a matching title,
path prefix or branch name. Task adoption requires its canonical lease/binding;
unassigned host sessions cannot be adopted as task runners. Record the association
provenance; changing it must not silently rebind an active process.

### Project and root contract (proposed; names may follow existing conventions)

- Project: stable ID, label, primary root ID, declared roots, optional Anvil
  connection, default Pi owner, default execution mode.
- Root: stable ID, label, owner host/runtime, configured absolute path, enforced
  read/write grant for scoped runners (context-only for host mode), repository identity if applicable. Paths stay server-side.
- Thread reference: source + owner + native session ID, principal/project,
  selected worktree, immutable initial root binding, optional task/run reference.
- Worktree: repository/root ID, stable owner reference, branch/base, status and
  whether it is existing or managed. An Anvil task worktree remains lease-owned.

Exactly one primary root sets new-session cwd and default Git context. Secondary
roots can be separate repositories or non-Git context. Anvil State checkout
identity remains explicit; do not initialize State for each added directory.
Project root edits affect new sessions; active sessions retain their declared
binding unless a separately reviewed rebind is supported. No global `chdir`.

Offer a project editor that selects server-declared roots, marks a primary and
shows access modes. Registering new filesystem roots remains an operator action,
not arbitrary browser path entry. Support lazy file trees, bounded text previews,
file mentions, working/staged diffs and branch/worktree status. Search is scoped
and bounded. Reject traversal, escaping symlinks, special files and secrets;
handle binary/large files with explicit limits. Enforce this in the owner API,
not just hidden tree nodes. File context visible in the UI must match what the
agent can actually access; multi-root chat context requires real runner support,
not only putting path names in a prompt.

For isolated tasks, primary root is writable in the managed task workspace;
secondary roots default read-only, with an explicit opt-in to write access.
**Writable secondary roots are required delivery scope**, including coordinated
claims, conflict detection, isolated workspaces, per-root patch capture and
verification. T03d proves the canonical Anvil contract before T03e/f implement it;
the current single-root binding is not sufficient. Starting a partially claimed
root set or mounting a shared secondary checkout writable is forbidden. The initial
managed implementation targets Git roots on the same execution owner; another
host or non-Git writable root must report an explicit unsupported contract, not
silently downgrade requested access. Read-only non-Git context remains supported.
Host Pi retains existing operator authority and may edit selected secondary roots
within that authority; UI selection alone cannot enforce write denial elsewhere.
Worktree creation is an explicit action; it never switches the shared checkout.
Worktree deletion and arbitrary terminal exposure are outside initial scope.

### Run discovery contract

Keep operation intents authoritative for operations. Add read-only projections
from existing benchmark job owners, Pi session owners, Anvil task bindings and
declared historical evidence catalogs. Prefer supported list/status/artifact
contracts; if an owner has only status-by-ID, add bounded enumeration there first.
Do not query other owners' SQLite or scan unrestricted home/results directories.

Projected fields: source, owner, native ID, kind, title, timestamps, native state,
normalized status, project/task/thread references, target identity, evidence
references, verification state, observed-at and freshness. Use a namespaced key
such as `(owner, source, native_id)`. Dedupe operation/job representations only
with an explicit correlation; matching labels or timestamps are insufficient.
Imported artifacts retain provenance and “historical/imported” labels.

Bound every owner call independently. Proposed starting bounds: 2-second source
read deadline, at most one in-flight read per source and four total, 50 rows per
page, bounded response bytes using the existing adapter limits. Cancel timed-out
reads; never wait for all owners before presenting successful ones. Validate and
tune these bounds against the chosen transports without dropping the UI freshness
budgets. A hung owner becomes stale/unavailable while others continue refreshing;
backoff applies to that owner, not the whole page. Cancellation and navigation
must release polling and concurrency slots.

Use a stable snapshot/watermark with cursor order `(updated_at, namespaced_id)`,
or the owner's equivalent; sorting mutable timestamps alone is insufficient.
retain terminal state rather than inferring failure from absence. Cache is a
projection, rebuildable from owners. Authorization precedes filtering, counts,
search and cursor construction; one user's cache must not leak another's runs.
Report partial coverage per source and preserve last-good rows as stale.

## 4. Scope, sequencing and migration

The first release covers the requested chat home, multi-directory configuration
with explicitly selected writable secondary roots,
scoped file/diff viewing, worktree visibility/selection, supported Pi settings,
live run discovery, plan reading and task launch. It is not a new editor/terminal,
agent scheduler, router, State engine or general event platform.

Extend existing Python stdlib services and vanilla JS modules; do not introduce
frameworks or duplicate Pi's Node runtime inside Python. A Pi Web modification
belongs in a pinned source package/patch pipeline with license attribution and
rebuild tests, never edits to installed `node_modules`. Private configuration and
provisioning belong in their respective operator and infrastructure repositories.

Migrate each existing single-checkout project to one primary root without changing
its ID or State identity. Preserve old routes, Model test history, Pi sessions,
claim/evidence bindings and service credentials. Record config schema versions,
backup/rollback and old-version compatibility before changes. Pilot one declared
project and retained session; do not migrate/claim arbitrary historical sessions.

T08 performs the release-readiness workflow only after implementation and explicit
live authorization. Authentication, runner admission or State recovery blockers
can leave deployment blocked while unaffected packets proceed. Never claim full
completion using a mockup, mocked CLI, unit tests alone or a standalone Pi link.
