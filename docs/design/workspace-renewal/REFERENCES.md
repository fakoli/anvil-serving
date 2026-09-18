# Workspace renewal implementation references

This is a source map for the eight **proposed** packets in
[tasks.json](tasks.json) and [PLAN.md](PLAN.md).  It records the current
checkout at `3048abeb2a066c159760e0e9ea168b801a078b84`, which matches the
packet source base.  Source links are pinned to that public repository revision so they also work
on the documentation site; symbol names are the stable local search anchors
and line numbers are verified at that revision.

Read the packet and this map before changing a seam.  None of the routes or
behaviour described as an intended change exists merely because it appears
below.  Preserve the existing owner/lease boundary, bounded subprocess policy,
and separate Anvil acceptance throughout.

## T01 — Restore trustworthy plan reads

**Reading order.** [T01 packet](tasks/T01.md) → [`Projects`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L65) → [project view](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/project_work.js#L8) → [project tests](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_projects.py#L226).

- Current read path: [`run_bounded`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L21) drops stderr and raises `project_source_unavailable` for every nonzero exit; [`Projects.cli`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L82) only parses stdout after that succeeds.
- [`Projects.prd`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L120) calls supported `prd show --json --limit`, bounds UTF-8 content, and returns content/digest/revision. Reuse that contract; do not add a raw-file fallback.
- [`projectWorkView` / `readPlan`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/project_work.js#L8) loads the selected PRD and preserves the existing Markdown renderer. It is the UI error-category/retry seam.
- Reuse [`test_process_timeout_and_output_bounds`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_projects.py#L226) and [`test_plan_content_uses_supported_scoped_bounded_cli`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_projects.py#L236); add structured nonzero-output coverage beside them.
- Gotcha: investigate `projection_not_converged` in Anvil through supported read-only diagnostics first. A successful CLI command is the only source of a readable persisted plan; no claim or repair belongs in this packet.
- Start with caller inventory for `run_bounded`; it also backs status, task, packet, claim, and evidence reads through `Projects.cli`.
- Keep stdout byte and timeout limits when adding a typed structured-error channel.
- Treat malformed JSON, non-JSON exits, and oversized output as generic/redacted failures.
- Persist only the successful source digest/revision already returned by `Projects.prd`.
- The project view's PRD request has a 30-second client timeout; keep any retry bounded.
- Review outcome: an Anvil State diagnosis can be a **blocked** T01 receipt, not completion; T07 remains blocked while a reader cannot render the persisted plan.

## T02 — Prove the Pi Web integration seam

**Reading order.** [T02 packet](tasks/T02.md) → [Pi Web config](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L100) → [installer plan](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L332) → [shell routing](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L543) → [Pi Web tests](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_web.py#L112).

- Current managed-service seam: [`PiWebConfig` / `pi_web_config`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L100) validates exact, bounded configuration; [`unit_content`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L282) binds the managed service to `127.0.0.1`.
- [`plan`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L332), [`probe`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L369), and [`PiWebInstaller.install`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_web.py#L478) are lifecycle/install code, not an existing browser integration API.
- [`Console.read`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/console.py#L373) and [`WorkbenchService.read`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L215) expose authenticated server reads; inspect their route/auth checks before proposing a context handshake.
- Reuse Pi Web safety tests: [`test_config_rejects_unsafe_or_undeclared_fields`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_web.py#L141), [`test_unit_content_refuses_a_non_loopback_bind`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_web.py#L198), and [`test_probe_url_is_loopback_only`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_web.py#L506).
- Gotcha: this packet chooses and proves one transport only. Framing a host-owned Pi service must not turn it into a managed task runner or bypass origin, CSRF, CSP, session-owner, or service-worker rules.
- Begin with `PiWebConfig`, because configuration is the existing authority declaration.
- Use synthetic data for the spike; no Pi Web installer call is necessary to inspect the seam.
- `PiWebInstaller.install(confirm=False)` is a plan path, not permission to mutate a service.
- Record child response headers and browser transport observations in the decision receipt, not this public source map.
- Keep an explicit owner-host label if the service cannot prove per-principal session isolation.
- A blocked decision is useful evidence and leaves T03/T05 blocked by design.

## T03 — Add multi-directory project bindings

**Reading order.** [T03 packet](tasks/T03.md) → [config validation](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/config.py#L24) → [project preparation](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L148) → [`PiRunnerPolicy`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_runner.py#L31) → [artifact sandbox](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/task_artifacts.py#L112).

- Current config permits one `checkout`, one `anvil_binary`, and optional `runner_root` per project in [`validate_config`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/config.py#L24); [`Projects.public_project`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L96) deliberately reveals only public identifiers.
- [`Projects.prepare`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L148) creates/reconciles one claim and provisions its isolated workspace. Extend the declared configuration and this shared preparation path rather than letting browser input select a path.
- [`TaskArtifacts.provision`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/task_artifacts.py#L133), [`capture`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/task_artifacts.py#L149), and [`_in_scope`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/task_artifacts.py#L282) are the existing managed-workspace and artifact boundaries.
- Reuse [`test_browser_cannot_select_another_checkout`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_projects.py#L57), [`test_linked_git_worktree_is_not_a_runner_checkout`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_runner.py#L30), [`test_capture_rejects_symlink_before_private_artifact_write`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_task_artifacts.py#L23), and [`test_capture_checks_patch_paths_against_frozen_anvil_scope`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_task_artifacts.py#L36).
- Gotcha: project roots in host Pi Web mode are context/navigation until T02 proves enforcement. Do not call UI filtering a filesystem sandbox; retain old session bindings unchanged.
- Begin with a backwards-compatible single-root parser and preserve the project ID.
- Stable root IDs belong in declared configuration, never in browser-provided paths.
- Apply scope checks before a filesystem/tool operation, not only while rendering an explorer.
- Keep one primary root for new task sessions; existing cwd/worktree bindings stay immutable.
- Treat a linked worktree as an identity to verify, not a path-prefix match.
- Add adversarial path cases beside the present runner/artifact security tests.
- Writable secondary roots are now required scope. [`PiTaskBinding`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_sessions.py#L37), the single-claim `Projects.prepare` path, `PiRunnerPolicy` and `TaskArtifacts` all need coordinated review; adding bind mounts alone is insufficient. T03d records exact separate Anvil owner seams before T03e changes them; no multi-root claim command is asserted to exist today.

## T04 — Simplify navigation and decouple page health

**Reading order.** [T04 packet](tasks/T04.md) → [navigation setup](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L107) → [`refresh`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L543) → [CSS navigation rules](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/workbench.css#L161) → [frontend contracts](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_frontend_contracts.py#L7).

- Navigation already has [`syncNavigation`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L181), [`closeNavigation`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L196), Escape handling, and a mobile `nav-open` class. Extend these; do not add a second menu state machine.
- [`refresh`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L543) currently fetches fleet/settings/catalog/operations together and throws if fleet fails before rendering page content. [`scheduleResume`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/observatory.js#L775) excludes Workbench, Playground, and Work from shell polling.
- Preserve `AbortController`, generation checks, route parsing, and `ctx` view inputs; a HUD/fleet refresh must not replace active chat/work DOM or drafts.
- Reuse [`test_workbench_shell_uses_authenticated_owner_views_and_no_synthetic_compute_surface`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_frontend_contracts.py#L7) and add behavioural browser/module coverage, not source-string-only tests.
- Gotcha: `frame-ancestors` is about who embeds this shell, while a T02 frame needs the shell's `frame-src`; keep those policies narrowly scoped.
- Start by writing down which current navigation behaviour must remain bookmarked.
- Keep `parseRoute` as the route authority and make drawer state a view concern.
- Split optional telemetry failures from the view's own required reads in `refresh`.
- Preserve `document.hidden`, dialog, focus, and abort checks around every refresh change.
- Reuse existing CSS breakpoints before adding layout rules.
- Test Escape/focus return and narrow layout in a browser; JavaScript module tests alone cannot prove either.

## T05 — Make Playground the Pi chat home

**Reading order.** [T05 packet](tasks/T05.md) → [Playground](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/playground.js#L7) → [task Pi chat](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/pi_chat.js#L205) → [Pi routes](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L324) → [session store/service](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_sessions.py#L132).

- [`playgroundView`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/playground.js#L7) is today a direct-model conversation UI with its own catalog/history. Retain it as Model test; it is not currently a Pi-session home.
- [`piChatView`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/pi_chat.js#L205) already provides task-bound list/events/create/resume/stop/branch/command flows; use its draft, transcript, and pending-extension helpers instead of rebuilding them.
- [`WorkbenchService._pi_read`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L324) requires both project and task for task-session reads; [`_pi_mutate`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L350) owns task-session mutations.
- Reuse [`PiSessionStore.create`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_sessions.py#L132), [`PiSessionService.command`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/pi_sessions.py#L562), and [`test_pi_chat_keeps_drafts_isolated_and_transcript_ordered`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_chat_behavior.py#L14).
- Gotcha: native host sessions and task-bound Pi sessions have different ownership and authority. Keep native IDs/correlation references; do not copy transcripts or attach a second writer.
- Start only after the passed T02 receipt names the supported host integration contract.
- Keep the current Playground conversation IDs and direct model request flow intact under Model test.
- Use `conversationDraft` to keep drafts keyed by task/session instead of a new browser store.
- Preserve extension request IDs using the existing transcript-control helpers across event refreshes.
- Route task commands through `WorkbenchService`, which revalidates the current binding server-side.
- A disabled unavailable feature with its reason is preferable to an optimistic client control.
- Follow T05's capability matrix: host Pi Web provides the full experience, while the current managed dispatcher lacks queued follow-up and attachment commands. Those managed extensions are explicitly deferred, not silently counted as host/task parity.

## T06 — Make Workbench runs discoverable and current

**Reading order.** [T06 packet](tasks/T06.md) → [Workbench view](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/workbench.js#L50) → [Console operations read](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/console.py#L373) → [benchmark job schema](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/benchmarking/jobs.py#L243) → [fleet workload readers](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/fleet_workload_sources.py#L131).

- [`workbenchView`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/workbench.js#L50) renders only `ctx.operations`; [`Console.read`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/console.py#L373) is the current route dispatcher for retained console operations.
- [`validate_job_spec`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/benchmarking/jobs.py#L243), [`new_job_record`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/benchmarking/jobs.py#L284), [`validate_job_record`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/benchmarking/jobs.py#L305), and [`transition_job`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/benchmarking/jobs.py#L325) define benchmark-owner identifiers/state validation to reuse for a read projection.
- [`build_fleet_workload_readers`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/fleet_workload_sources.py#L131) is a bounded, topology-validated owner-reader pattern; it is not evidence that every job/run is already enumerated.
- Reuse [`test_partial_sources_and_offline_hosts_survive`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/observability/test_observatory_operations.py#L295) and [`test_status_and_cursor_logs_survive_restart`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/control_plane/test_benchmark_jobs.py#L66); locate the actual missing run owner before changing UI filters.
- Gotcha: authorize before counts/cursors/cache, namespace identifiers, and dedupe only explicit correlations. A completed Pi session or imported evidence is not a benchmark pass or promotion.
- Start with a representative missing run and write down its owner before designing an aggregator.
- Model each source's native state and freshness separately from a normalized presentation state.
- Give every source reader an independent deadline and retain its last-good rows as stale on failure.
- Add cursor tests for equal timestamps and identifier collisions before raising the current row cap.
- Keep experiment launch links; T06 changes discovery, not launch authority.
- Compare must reject incompatible dimensions rather than silently normalizing measurements.

## T07 — Connect plans and tasks to Pi execution

**Reading order.** [T07 packet](tasks/T07.md) → [project task detail](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L129) → [preparation](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L148) → [service mutations](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L279) → [work view](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/project_work.js#L8) → [evidence jobs](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/evidence_jobs.py#L33).

- [`Projects.task_detail`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L129) supplies packet, PRD, active claims, and exact readiness reason. [`prepare`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L148) is the idempotent claim → isolated workspace path and must remain the only start path.
- [`WorkbenchService.mutate`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L279) dispatches project/artifact/Pi mutations; [`_pi_mutate`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L350) ties session creation to the prepared binding.
- [`projectWorkView`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/observability/dashboard/static/views/project_work.js#L8) already has plan selection, task detail, task Pi tab, and evidence controls. Make its selection/deep-link behaviour richer before inventing another task surface.
- [`EvidenceJobs.start`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/evidence_jobs.py#L33) serializes bounded review/verify/submit/release work; reuse [`Projects.review_evidence`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L292), [`verify_evidence`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L301), and [`submit_evidence`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L325).
- Gotcha: do not auto-ready a task, replay an uncertain start, resubmit uncertain evidence, or conflate verification with independent Anvil acceptance. Use a disposable ready fixture for UI demonstrations.
- Start from `task_detail.execution`, which already centralizes user-visible admission reasons.
- Reuse the caller-provided `request_id` as the persisted start idempotency key; the stored request digest detects a conflicting reuse of that key. Never generate a replacement after uncertainty.
- Stop the runner through the managed path before capture or transfer; `TaskArtifacts.ensure_quiescent` enforces this.
- Keep frozen packet verification commands from the prepared binding, not editable browser content.
- Link the returned native binding/session identifiers across Work, Playground, and Workbench.
- The evidence worker has a fixed capacity of two; present busy/failed state rather than queueing hidden work.

## T08 — Validate migration and deliver the release

**Reading order.** [T08 packet](tasks/T08.md) → [validation journeys](VALIDATION.md) → [packaging tests](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_packaging.py#L11) → [official Pi smoke](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_official_smoke.py#L22) → [release readiness skill](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/skills/anvil-serving-release-readiness/SKILL.md).

- [`VALIDATION.md`](VALIDATION.md) is the acceptance checklist and remains planning evidence until each journey is measured and receipted.
- Reuse [`test_pi_build_uses_packaged_pinned_sources_and_never_the_companion_checkout`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_packaging.py#L11) and [`test_runner_assets_are_byte_identical_to_reviewed_sources`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_packaging.py#L24) to catch installed-file drift.
- Reuse [`test_official_pi_native_history_fork_resume_and_model_controls`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/tests/workbench/test_pi_official_smoke.py#L22) as the pinned-Pi lifecycle smoke; it does not prove browser integration, migration, or live deployment.
- Existing configuration validation in [`validate_config`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/config.py#L24) is the migration compatibility seam; keep single-root project IDs and retained bindings stable.
- Gotcha: release/deployment scope invokes the named release-readiness skill and needs separate live authorization. Local tests never imply endpoint parity, retained-session recovery, or production success.
- Begin by auditing packet receipts and dependency gates; do not infer completion from this planning document.
- Run the public secret/identity review before packaging any newly introduced config or evidence fixture.
- Clean-install evidence must use pinned sources; a hot patch to installed files is a release failure.
- Exercise old single-root configuration and retained IDs in isolated migration coverage before live work.
- Keep local, staged, and live acceptance results distinct in the final receipt.
- Rollback proof must preserve the existing operator service/session state and remains separately authorized.

## Cross-packet guardrails

- Trace all callers before altering [`run_bounded`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L21), [`Projects.cli`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/projects.py#L82), [`WorkbenchService.read`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L215), or [`WorkbenchService.mutate`](https://github.com/fakoli/anvil-serving/blob/3048abeb2a066c159760e0e9ea168b801a078b84/anvil_serving/workbench_app/service.py#L279); each is a shared trust boundary.
- Keep configuration, real paths, topology, credentials, owner sessions, and deployment evidence private. Public tests/docs use synthetic identities and generic loopback addresses only.
- The dependency gate is real: T03 and T05 require a recorded, passing T02 decision; T07 follows T01/T03/T05/T06; T08 validates completed evidence rather than marking proposed work done.
- Prefer the narrowest existing service, project, or session seam before introducing a route or persistent store.
- Keep browser state limited to presentation preferences and drafts; server-owned bindings remain authoritative.
- Every packet receipt records measured result, remaining blocker, and the next eligible packet without changing proposal status prematurely.
