# Workspace renewal UX notes

This note records read-only observations and proposed corrections for the
static [clickable prototype](index.html). It is not source code, a live-product
claim, or implementation authorization. See also the [prototype guide](GUIDE.md).

## Observed

- Pi Web uses a left sessions/explorer rail, central transcript and composer,
  optional right file/diff panel, plus model controls and settings.
- Workbench retains a large experiment form, six tabs (Overview, Run flow,
  Compare, Evidence, Events, All runs), a large comparisons area, repeated
  system/section context, and eight retained operations. Current code excludes
  Workbench from shell auto-refresh.
- Open WebUI exposes home search, folders, Notes, Workspace, and a composer
  More menu for uploads, webpages, files, notes, knowledge, and reference chats.
  Its Controls surface has System Prompt plus long advanced/runtime parameters;
  Workspace has Models, Knowledge, Prompts, Skills, and Tools catalogs.
- No send, upload, save, or other write was performed during these observations.

## Proposed now

- Keep Pi's familiar shape. Add only the thin Anvil shell, project context, and
  source-labeled removable context chips through one picker.
- Offer project-relevant new-chat starters. Context selection must show source
  and must never widen filesystem or tool authority.
- Use one Settings surface with **This thread**, **Project defaults**, and
  **Pi service** tabs. Put advanced supported controls behind progressive
  disclosure; do not expose raw runtime/router parameters as chat preferences.
- Make Workbench lead with active/recent runs, source freshness, stable detail,
  and source-unavailable states. Preserve existing experiment launch access,
  but do not make its large forms the default activity view.
- Make Anvil Work lead with readable plan/task selection and a start preview:
  packet, workspace, model, roots, claims, and verification. It must explain
  blockers rather than changing task readiness.
- The fixture initially shows two selected writable Git roots: primary
  `product-ui` and secondary `service-api`. Actual canonical claims, isolation,
  and per-root enforcement are intentionally absent.

## Packet alignment

| Packet | UX correction |
| --- | --- |
| T01 | Show `projection_not_converged` as a plan-read problem; do not fall back to disk or repair State from the UI. |
| T03 | Root picker, explorer/worktree context, selected writable secondary root, and host/task authority disclosure. |
| T04 | A compact hamburger shell without duplicating context in every page header. |
| T05 | Pi-first Playground, ordinary versus Anvil-task threads, settings scopes, context chips, and familiar file/diff work. |
| T06 | Current runs first, bounded source status/freshness, detail, and retained evidence instead of a default experiment dashboard. |
| T07 | Readable plan, blockers, preview-before-start, and task/Pi/run links. |

## Deferred, only with a scoped contract

- Notes, knowledge, referenced chats, saved prompts, dictation, and voice may
  borrow Open WebUI's discoverability patterns later.
- Each needs Pi-owner capability, provenance, privacy, context-limit, and
  authorization decisions. Attachments and managed-task capabilities remain
  separately gated.
- Do not add blind raw-parameter passthrough, a second provider or session
  authority, or an Open WebUI clone/stack.

## Compatibility and review

Use **Pi** as the product label while retaining public `#/playground` and Model
test compatibility. Review hierarchy, labels, authority disclosure, and narrow
viewport behavior. The root records screenshots and validation separately; this
note records neither as completed evidence.
