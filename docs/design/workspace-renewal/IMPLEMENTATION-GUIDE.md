# Engineering handoff: where to start and what to build

This supplements the task packets for an engineer unfamiliar with the subsystem.
Read only the section for your task plus its referenced contracts. The examples
are design aids: they are not a patch, new production API, or deployment authority.

- [Verified source map](REFERENCES.md): files, symbols, line numbers and existing tests.
- [Request and data examples](examples/contracts.json): explicitly labels current
  versus proposed contracts; synthetic values only.
- [Executable pure examples](examples/reference_examples.py): error projection,
  primary-root invariant and namespaced run identity, with negative self-checks.
- [External references](UPSTREAM-REFERENCES.md): primary sources, intended use and
  version caveats. Existing installed contracts take precedence over upstream main.

Use the [visual mockups](mockups/GUIDE.md) and [UX corrections](mockups/UX-NOTES.md)
for hierarchy and interactions. Reuse product components; do not ship the synthetic
HTML or treat its controls as evidence that a backend capability exists.

Run the examples from the repository root:

```sh
python3 docs/design/workspace-renewal/examples/reference_examples.py
```

This checks examples only. Follow each task's existing test commands for product
changes. Do not make the application import files under `docs/`.

<a id="t01"></a>

## T01: preserve the error, then resolve the State cause

Current call chain:

```text
project_work.js readPlan
  → workbenchRequest(projects/{project}/prds/{prd})
  → WorkbenchService.read → Projects.prd → Projects.cli → run_bounded
  → configured Anvil binary: prd show <id> --json --limit 2097152 --cwd <checkout>
```

There are two bugs to address separately: a real `projection_not_converged`
response, and the adapter hiding that response. Fixing error visibility will not
repair the project. Do not replace a failed canonical read with a disk-file read.

Minimal implementation approach: retain the bounded subprocess runner and add a
narrow structured failure carrying captured stdout privately. Catch it only in
`Projects.cli`, translate the known read-error schema to fixed public text, and
otherwise preserve the generic error. Do not let a nonzero exit become success.
`project_read_error` in the runnable examples demonstrates that translation.
Use the same mapping for a returned `ok:false` envelope even with exit code zero.

A patch-shaped sketch, **not a complete implementation**:

```python
# Proposed subtype (also exercised in reference_examples.py):
class BoundedCommandFailure(ObservatoryError):
    def __init__(self, stdout):
        super().__init__("project_source_unavailable", "Anvil could not complete this read.", 409)
        self.stdout = stdout  # Private transport data; never log/serialize attributes.

# In run_bounded's existing `if code:` branch:
raise BoundedCommandFailure(bytes(output))  # proposed; stdout is not message text

# In Projects.cli (preserve its successful-byte return contract):
try:
    raw = self.run(argv, cwd=project["checkout"])
except BoundedCommandFailure as failure:
    raise project_read_error(failure.stdout) from None
```

The new exception should subclass the existing safe `ObservatoryError` (or keep
an equivalent safe boundary), keep raw output out of `args`, `str`, `repr` and logging, and preserve
cleanup in `finally`. Existing direct Git cleanup calls and injected test runners
must still work. Reuse `strict_json`, `identifier` and `ObservatoryError`; reject
JSON arrays and malformed envelopes instead of calling `.get` blindly.

First regression: an actual bounded subprocess exits nonzero with a typed JSON
error; the public result contains its mapped code but none of a sentinel private
message. Add unknown-code, oversized-output and timeout cases to existing tests.
Keep underlying projection diagnosis/repair in the Anvil repository and owner.

<a id="t02"></a>

## T02: prove one Pi Web mounting strategy

Do this before designing a host-session proxy API. Read `PI-REUSE.md`, the managed
`pi_web.py` installation/status code, and the exact installed package's server,
auth, session and asset routes. Pin any experiment to that package identity.

The integration decision example starts **blocked**. Turn it passed only after
recording the selected transport and positive/negative ownership checks. Record
both UI transport and session authority; an iframe loading is only one check.

Proposed header delta for a separate-origin experiment (synthetic origin):

```http
# Parent shell: retain other directives, add one exact permitted frame source.
Content-Security-Policy: ...; frame-src https://pi.example.test; ...
# Pi child: only if consistent with the reviewed edge/auth policy.
Content-Security-Policy: ...; frame-ancestors https://console.example.test; ...
```

The ellipses make these non-deployable sketches. Inspect every response layer for
conflicting CSP/X-Frame-Options, login redirects and service workers. Never paste
a replacement policy or assume parent permissions override child protections.
Do not add `postMessage` until a real context-handshake need is established. If
needed, version it, check `event.origin` AND `event.source`, accept only declared
IDs, and treat every ID as input requiring server authorization. No credentials,
filesystem paths, raw commands or wildcard target origins belong in it.

Keep the current isolated task RPC path until the chosen Pi Web seam can enforce
that exact runner/lease binding. Owner-host mode remains visibly unconfined;
project filters cannot be sold as tool-level confinement.

<a id="t03"></a>

## T03: multi-root projects without a second project system

Use the example project as a **partial target fragment**, not current config:
`validate_config` currently rejects additional keys. Update schema validation,
migration, public projection, runner binding and explorer through T03's ordered
slices; do not publish partial capability as complete. The fragment omits legacy
fields for brevity; it must be merged with their preserved values, never replace
the existing project object.

Migration recipe:

1. Preserve existing `id`, `resource_id`, `checkout`, `anvil_binary`, `runner_root`.
2. Materialize one stable primary-root record from `checkout`; keep checkout as
   the explicit State connection during compatibility transition.
3. Reject an explicit new root configuration that conflicts with legacy checkout;
   do not silently choose one. Running the migration again must produce no change.
4. Add only server-declared secondary roots. Validate primary membership and
   duplicate IDs using the demonstrated invariant; enforce bounded root count.
5. Freeze effective roots/cwd/mode when starting a session. Config updates affect
   new sessions. Migrate display associations separately from execution bindings.

Public project JSON deliberately omits absolute paths and secret references.
`task_access` is a proposed policy field; it only becomes a promise after runner
mount/tool tests prove it. Host-mode public rows must say context-only/operator
access, not reuse the isolated-mode read/write badges.

Explorer design boundary: accept `(project_id, root_id, relative_path)` or opaque
file IDs, resolve a configured owner, and authorize before touching the filesystem.
Reject encoded traversal and symlink escape. `Path.resolve()` plus a prefix test
is not a complete safe-open implementation because files can change between
check and open. Reuse the project's existing safe artifact/sandbox routines where
applicable; require a tested platform-aware owner implementation before exposing
host reads. Do not include a misleading five-line “sandbox” in this change.

**Writable secondary roots: required owner work, not a config-only extension.**
Current `Projects.prepare` stores one `lease_id`, `claim_worktree`, baseline and
verification checkout. `PiTaskBinding` in `pi_sessions.py` and `PiRunnerPolicy`
in `pi_runner.py` are single-workspace contracts. `TaskArtifacts` captures and
transfers one patch. Trace all these consumers before changing their shapes.

T03d must inspect the separate Anvil repository and produce an independently
reviewed `T03d.multi-root-contract.json` alongside its private receipt: versioned
owner API/CLI shapes (clearly new versus existing), exact source/test paths,
normalized repository identity/conflict scope, errors, recovery transitions and
deterministic fixtures. Prefer one canonical task with an Anvil-owned root-set
claim; never emulate claims in the Serving store or initialize competing State
projects. If the installed owner lacks that contract, implement it in T03e
through supported Anvil APIs before wiring Serving. No invented CLI flags.

Required semantics for the proposed contract:

- Server-authorized root IDs select a frozen set. Each writable Git root records
  repository identity, canonical claim reference, baseline SHA, scoped paths,
  private runner/verification checkout and verification commands. Include this
  entire set in binding/policy/request digests; preserve single-root compatibility.
- Canonical conflict checks recognize the same repository through different
  projects/paths. All roots must be claimed and provisioned before any Pi writes.
  Partial acquisition starts no runner; reconcile uncertain responses with the
  original request identity, release only known acquired claims, retain cleanup
  state, and never delete dirty work as compensation.
- Mount isolated full clones of each writable repository, never shared checkouts
  or linked worktrees exposing external Git metadata. Primary determines cwd;
  root IDs distinguish equal relative filenames. Deny overlapping/aliased mount
  roots. Any expired/revoked root authority stops the session before release.
- Capture and review a manifest of `(root_id, baseline, patch_digest)`; freeze
  verification commands with their root. The reviewed manifest digest covers all
  roots. Verification must run in each isolated verification checkout and include
  declared cross-root checks when required; one root's success cannot cover another.
- Transfer may fail between repositories: no atomic multi-repository Git promise.
  Persist per-root progress, block submission on a partial transfer, reconcile
  before retry, and retain artifacts. Never auto-revert another writer's changes.

T03f tests must include two roots with the same filename, read-only third root,
conflicting claim, second-root acquisition failure, lost start response, lease
loss during execution, altered secondary patch after review and second-root
transfer failure. Until these pass, show requested write access as blocked;
do not call read-only degradation fulfillment of the owner's requirement.

<a id="t04"></a>

## T04: extend the shell already present

`observatory.js` already has `syncNavigation`, `closeNavigation`, mobile focus
handling, `nav-open` and `menu-toggle`. Extend those for desktop collapsed/pinned
mode; keep only one source of truth for menu state. Use a nonmodal pinned desktop
rail; use the existing modal focus behavior for the mobile drawer.

Make a page's essential reads explicit. For Playground, session/catalog/thread
availability governs the page; fleet telemetry is secondary. Keep the current
safe session/authentication flow, but move optional HUD results into independently
updated DOM nodes. `Promise.allSettled` alone is insufficient if rendering still
waits for a hung fleet request. Use bounded independent updates tied to the route
AbortSignal and generation, rejecting late results for a replaced route.

Layout sketch; adapt existing selectors and responsive breakpoints:

```css
/* Proposed pane geometry, not final product styling. */
.chat-workspace { display: grid; grid-template-columns: 16rem minmax(0, 1fr); min-height: 0; }
.chat-main { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-height: 0; }
.chat-transcript { overflow: auto; min-width: 0; }
@media (max-width: 48rem) { .chat-workspace { grid-template-columns: minmax(0, 1fr); } }
```

The enclosing route must allocate remaining viewport height; otherwise `minmax`
will not keep the composer visible. Verify with long output and an open keyboard.

<a id="t05"></a>

## T05: compose existing chat capabilities

Use the display label **Pi** and retain `#/playground`; show Conversations/Model
test tabs plus stable
source/owner/native-session selection. Do not append a host path or secret to a
URL. Existing `piChatView` requires `projectId`, `taskId`, `detail`; calling it
without a task does not create a general-purpose host-session view.

Follow [T05's capability matrix](tasks/T05.md) for host versus managed task scope.
Current managed commands lack queued follow-up and attachment payloads; don't
derive support from upstream RPC documentation. For managed task chat, reuse `conversationDraft`, `transcriptItems`,
`shouldPreserveTranscriptControls` and the existing cursor/poll behavior. Preserve
extension form values during streaming. For host chat, use the selected Pi Web
integration and its session authority; do not copy its JSONL files into PrivateStore.

Keep the Unassigned owner-only group. Association verifies repository/worktree
identity but does not grant permissions or reparent a task. Thread archive must
be reversible and delegated to a supported owner operation (or a namespaced view
preference); it must never call native delete under an “Archive” label.

Settings example descriptors distinguish scope, owner and apply timing. Derive
choices from the declared/current owner catalog. For example, upstream may expose
thinking levels that the installed server does not allow. Do not expand the
server enum merely because a web page lists more levels. Read back the effective
setting after a successful command; a transport acceptance is not proof that Pi
applied it. Prefer disabling model changes during active turns until supported
queue semantics are proven.

Use `workbenchRequest` for same-origin Workbench calls; it supplies CSRF, response
validation and session-expiry handling. A Pi Web transport stays at its own
reviewed boundary. Do not route third-party endpoints through this facade by
passing a browser-controlled base URL.

<a id="t06"></a>

## T06: make one external run appear, then generalize only as needed

First trace one known missing run: native owner ID → bounded owner list/status →
authorized projection → Workbench row → retained artifact. This is the vertical
slice; adding another static configured evidence card does not solve discovery.

Use `projected_run_id` in the examples for a deterministic, bounded namespaced
key; store the original owner/source/native ID separately. Owner-native IDs are bounded opaque UTF-8,
not restricted to Workbench route-token grammar; slash-containing IDs are valid
when that owner supports them. Encode them at the owner API boundary, never
concatenate them into a URL or filesystem path. It solves identity,
not deduplication. Merge operation and benchmark representations only when the
owner provides an explicit correlation. A mutable timestamp is not an identity.

Proposed list contract:

```json
{"items": [], "next_cursor": null, "sources": [
  {"id": "benchmark-owner", "status": "stale", "last_success_at": "2026-09-18T12:00:00Z"}
]}
```

Return last-good authorized rows with freshness independently of native run state.
Never change a completed run to failed because its owner stopped answering.
A cache must be scoped by principal and effective access policy, not URL alone.
Pagination needs a stable snapshot/watermark (or equivalent owner cursor); sorting
on changing `updated_at` alone can move rows across pages and skip them. Backfill
and live updates need distinct cursors. Expired cursors trigger an explicit reset.

Polling algorithm (pseudocode, not a ready-to-copy scheduler):

```text
per source: idle → request(with deadline) → success/timeout → schedule next
if hidden: cancel pending read and timer; retain rows as stale
if visible: refresh immediately; never overlap reads for the same source
on route disposal: abort + clear timers + remove visibility listener
on one source failure: back off only that source; healthy source loops continue
```

Use the existing API helper's AbortSignal/deadline and the Pi view's visibility
cleanup pattern. Ensure the server transport also enforces deadlines; cancelling
browser fetch alone does not stop a blocked owner call. Avoid `setInterval` with
async callbacks and avoid a shared `Promise.all` that waits for a hung owner.

<a id="t07"></a>

## T07: reuse the existing task launch; change the entry point

The `existing_task_start` fixture shows today's Workbench start body. Build it
from the authorized catalog and selected canonical task, never browser-supplied
checkout/model endpoint/lease values. Reuse the existing Pi view start flow.
Keep `request_id` stable through uncertain delivery and retry; mint a new one only
for an explicitly new logical start. Reconcile first when the returned result is
uncertain. Store session/task-binding correlation, not a second execution job.

Current task flow to preserve:

```mermaid
sequenceDiagram
  participant UI as Anvil Work / Playground
  participant WB as WorkbenchService
  participant State as Anvil State CLI
  participant Pi as Managed Pi runner
  UI->>WB: pi/sessions (task + declared model + request_id)
  WB->>State: validate packet and claim isolated worktree
  State-->>WB: lease and worktree identity
  WB->>Pi: start under frozen task binding
  WB-->>UI: session_id + binding_id
  UI->>WB: stop; review patch; verify; submit
  WB->>State: submit evidence for separate review
```

Do not implement a second claim/start path in `project_work.js`. Link the resulting
session to Playground and its projected run to Workbench. Use server readiness
rechecks at start, not just a disabled button. Show native blockers without
changing task status. Evidence view already has digest-bound operations; reuse it.

<a id="t08"></a>

## T08: package and hand over evidence

Start with the migration and retained-session fixtures, then install the built
artifact in a disposable environment. A successful source import does not prove
packaged JS/CSS or Pi assets exist. Use `test_packaging.py` and the release-readiness
skill, followed by independently witnessed browser journeys once deployment is
authorized. Record installed source and artifact fingerprints, config schema,
Pi pins, migration result and rollback result in a private receipt. Leave live
checks explicitly pending until measured; never fill them with a unit-test result.

## When to ask a lead engineer

Escalate with evidence when a proposed change widens filesystem/account authority
beyond the explicitly selected roots, cannot satisfy T03d's required multi-root
claim contract, changes the Anvil state model beyond that reviewed contract,
cannot preserve existing sessions, or requires replacing Pi Web rather than
integrating it. Include the smallest reproduction, rejected option, recommended
alternative and affected acceptance tests. Routine UI layout, helper reuse and
bounded test choices do not require another design meeting.
