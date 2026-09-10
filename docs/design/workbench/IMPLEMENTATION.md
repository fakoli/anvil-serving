# Project: Anvil Workbench production implementation

## Summary

Implement approved Concept 03 as an authenticated workspace for local AI experiments, model recipes, project tasks and Pi conversations, observability and compute operations. Preserve existing Serving, Connect and State ownership while replacing synthetic UI behavior with authorized real sources. The user explicitly delegated implementation decisions, approval, PR creation and merge on September 10, 2026.

## Goals

- Realize the approved user journeys with purposeful actions and truthful unavailable/error states.
- Reuse existing owner APIs and official Pi RPC with isolated session execution.
- Validate and independently review the integrated product, push a PR, merge and reflect.

## Non-Goals

- Model promotion, hidden route fallback, shared-secret copying, a new identity database or unrelated Connect changes.
- A permanent Open WebUI/Pi Web fork, arbitrary host administration, or automatic acceptance of agent-produced evidence.

## Requirements

- R001: US-SHELL-01: As an operator, I can move through Workbench, Playground, Models, Anvil work, Observability and Compute while retaining explicit host/model context and accessible settings/docs.
- R002: US-BENCH-01: As a researcher, I can create a deterministic evaluation from a declared suite, review its exact target and limits, follow actual operation phases, and compare retained evidence without using the evaluated model as judge.
- R003: US-PLAY-01: As a researcher, I can choose an authorized connector/model and saved preset, send a real prompt, cancel the request, inspect effective parameters and preserve a conversation without exposing provider credentials.
- R004: US-RECIPE-01: As an operator, I can inspect recipes, edit supported canonical candidate fields, review exact revisions and resource conflicts, and load/unload through the managed owner without implicit promotion.
- R005: US-WORK-01: As a project maintainer, I can select a configured exact checkout, read its real PRDs/tasks, inspect a work packet, start approved task work under a valid Anvil lease and attach actual evidence through State authority.
- R006: US-PI-01: As a task owner, I can create/select/resume/branch Pi conversation threads in task folders, choose an explicitly allowed model/thinking level, send/steer/abort, inspect tool events and respond to supported extension dialogs.
- R007: US-PI-02: As a task owner, I can recover a session after browser/process interruption without duplicate execution, lost claim ownership or credential exposure; the task runner enforces isolated checkout and declared resource limits.
- R008: US-OBS-01: As an operator, I can diagnose fleet/model/GPU/host/benchmark/log/monitoring health through scoped real measurements with source time, gaps and sufficient-sample labels; routine refresh consumes no model tokens.
- R009: US-COMPUTE-01: As an operator, I can select every configured host, see its declared workloads and Docker/native identities, inspect bounded logs/configuration, and review authorized start/stop intents at the owning controller.
- R010: US-EXEC-01: As an authorized operator, I can run a bounded command in an explicitly selected managed container after reviewing its identity and policy; native services without container exec show capability unavailability rather than host shell access.
- R011: US-SETTINGS-01: As an operator, I can save workspace preferences and session defaults, inspect service connections and storage ownership, manage Connect grants/revocation in Settings and read versioned Anvil documentation.
- R012: US-DELIVERY-01: As the maintainer, I receive a reproducible supported launch, verified installation and browser journeys, reviewed source merged through a pushed PR, and an honest reflection with improvement evidence and operational limits.

## Acceptance Criteria

- Every surface/action is mapped to the user stories below and a reproducible validation journey.
- Production UI loads only real authenticated owner projections; isolated fixtures are explicitly labeled and never substitute for live results.
- Source/packaging/security/browser gates and independent adversarial review pass before merge.
- Pi provider, context and runner bindings are explicit, durable and do not consume the model under test implicitly.

## Assumptions

### A001: Keep the existing Observatory facade and release boundary.

**Rationale:** Existing authorization, operation journals, metrics and Connect paths are proven; a new frontend and bounded modules avoid a disruptive parallel control plane.
**Requirements:** R001, R008, R009, R011

### A002: Reuse official Pi RPC while retaining the approved Pi Web-style chat.

**Rationale:** Source spike found Pi Web owns a second session/auth/path model. Official RPC exposes the required session/tool/extension operations without importing that authority.
**Requirements:** R005, R006, R007

### A003: Resource owners validate operations and retain current model assignments.

**Rationale:** Workbench does not implicitly unload, substitute, promote or grant host-wide execution. CPU-heavy builds are limited to 4 workers and approximately 4 GiB where supported.
**Requirements:** R002, R004, R009, R010

## Features

### F001: Workspace and settings

**Requirements:** R001, R011

### F002: Research and recipes

**Requirements:** R002, R003, R004

### F003: Project work and Pi

**Requirements:** R005, R006, R007

### F004: Observability and compute

**Requirements:** R008, R009, R010

### F005: Delivery and improvement

**Requirements:** R012

## Tasks

### T001: Add the authenticated Workbench extension seam

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/workbench_app/service.py, anvil_serving/workbench_app/config.py, tests/workbench/test_service.py

US-SHELL-01. Add the authenticated Workbench extension seam.

**Acceptance criteria:**

- New bounded routes reuse existing session, Origin, CSRF, scope and error behavior. Unconfigured capabilities are explicit.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_service.py -q`

### T002: Implement declared-model conversations and presets

**Feature:** F002
**Priority:** high
**Likely files:** anvil_serving/workbench_app/playground.py, tests/workbench/test_playground.py

US-PLAY-01. Implement declared-model conversations and presets.

**Acceptance criteria:**

- Only configured connector/model identities may be called; limits/cancellation/errors are real; secrets stay server-side and prompts remain user-scoped.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_playground.py -q`

### T003: Project State adapter and exact work packets

**Feature:** F003
**Priority:** high
**Likely files:** anvil_serving/workbench_app/projects.py, tests/workbench/test_projects.py

US-WORK-01. Project State adapter and exact work packets.

**Acceptance criteria:**

- Only operator-configured checkouts are resolved; reads and lease/evidence mutations go through Anvil CLI; draft PRDs and lease conflicts do not execute.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_projects.py -q`

### T004: Pi RPC transport and event contracts

**Feature:** F003
**Priority:** high
**Likely files:** anvil_serving/workbench_app/pi_rpc.py, tests/workbench/test_pi_rpc.py

US-PI-01. Pi RPC transport and event contracts.

**Acceptance criteria:**

- Bounded LF-framed transport correlates responses, normalizes text/tool/extension events, preserves prompt/steer/abort/model/thinking semantics and detects process failure.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_pi_rpc.py -q`

### T005: Pi isolated runner and durable sessions

**Feature:** F003
**Priority:** high
**Likely files:** anvil_serving/workbench_app/pi_sessions.py, tests/workbench/test_pi_sessions.py, pi_runner/package.json

US-PI-02. Pi isolated runner and durable sessions.

**Acceptance criteria:**

- Session identity binds principal/project/task/lease/runner; cursor recovery, new/resume/branch and extension replies are isolated; no browser path or command chooses a runner.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_pi_sessions.py -q`

### T006: Native and container workload projections

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/controller_adapter.py, tests/observability/test_controller_adapter.py

US-COMPUTE-01. Native and container workload projections.

**Acceptance criteria:**

- Declared native services join container serves with exact owner identities and bounded logs; existing reviewed lifecycle policy remains authoritative.

**Verification:**

- `python scripts/run_tests.py tests/observability/test_controller_adapter.py -q`

### T007: Managed container execution contract

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/control_plane/mcp/tools/services.py, anvil_serving/workbench_app/container_exec.py, tests/workbench/test_container_exec.py

US-EXEC-01. Managed container execution contract.

**Acceptance criteria:**

- Exec is explicitly permissioned, bounded and reviewed against managed container identity; no host-shell/path selection or privilege escalation is exposed.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_container_exec.py -q`

### T008: Build the production shell and status strip

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/static/observatory.js, anvil_serving/observability/dashboard/static/observatory.html, tests/workbench/test_frontend_contracts.py

US-SHELL-01. Build the production shell and status strip.

**Acceptance criteria:**

- Approved navigation/palette/single-scroll layout consumes actual authenticated sources; keyboard focus and drafts survive refresh; hidden pages stop polling.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_frontend_contracts.py -q`

### T009: Research views and recipe journeys

**Feature:** F002
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/static/views/workbench.js, anvil_serving/observability/dashboard/static/views/playground.js, anvil_serving/observability/dashboard/static/views/models.js

US-BENCH-01, US-PLAY-01, US-RECIPE-01. Research views and recipe journeys.

**Acceptance criteria:**

- Experiment review/status/compare/evidence use existing deterministic operations; model/preset/recipe controls bind exact owner identities and show blocked states.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_frontend_contracts.py tests/observability/test_runtime_experiment.py -q`

### T010: Observability and Compute workspaces

**Feature:** F004
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/static/views/observability.js, anvil_serving/observability/dashboard/static/views/compute.js, tests/workbench/workbench_ui.cjs

US-OBS-01, US-COMPUTE-01, US-EXEC-01. Observability and Compute workspaces.

**Acceptance criteria:**

- Dashboard groups expose real bounded sources; hosts/workloads retain exact scoped identities and meaningful log/control/exec journeys; missing metrics remain unknown.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_frontend_contracts.py tests/observability/test_observatory_logs.py -q`

### T011: Settings administration and documentation reader

**Feature:** F001
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/static/views/settings.js, anvil_serving/observability/dashboard/static/views/documentation.js, tests/workbench/test_documentation.py

US-SETTINGS-01. Settings administration and documentation reader.

**Acceptance criteria:**

- Persistent preferences/session defaults and real connection states appear; Connect access composes its existing adapter; bundled docs are safely rendered and versioned.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_documentation.py tests/observability/test_observatory_connect_access_ui.py -q`

### T012: Task workspace and Pi chat UI

**Feature:** F003
**Priority:** high
**Likely files:** anvil_serving/observability/dashboard/static/views/project_work.js, anvil_serving/observability/dashboard/static/views/pi_chat.js, tests/workbench/pi_ui.cjs

US-WORK-01, US-PI-01, US-PI-02. Task workspace and Pi chat UI.

**Acceptance criteria:**

- PRDs/tasks open real work packets and scoped Pi conversations; folders/threads, model/thinking, tools, steering, stop and supported extension UI work with recoverable state.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_frontend_contracts.py tests/workbench/test_pi_sessions.py -q`

### T013: Package and install the supported workbench

**Feature:** F005
**Priority:** high
**Likely files:** pyproject.toml, anvil_serving/observability/dashboard/app.py, docs/WORKBENCH.md, tests/workbench/test_packaging.py

US-DELIVERY-01. Package and install the supported workbench.

**Acceptance criteria:**

- Wheel contains all assets/docs and optional pinned Pi runner setup; a short supported command launches the authenticated workbench; private configuration stays outside Git.

**Verification:**

- `python scripts/run_tests.py tests/workbench/test_packaging.py -q`

### T014: Verify journeys, adversarial review and merged delivery

**Feature:** F005
**Priority:** high
**Likely files:** docs/design/workbench/IMPLEMENTATION-VALIDATION.md, docs/design/workbench/REFLECTION.md

US-DELIVERY-01. Verify journeys, adversarial review and merged delivery.

**Acceptance criteria:**

- Every story and rendered action has an acceptance journey; scoped and full gates pass, independent review findings are corrected, live checks are recorded and exact reviewed head is merged.

**Verification:**

- `python scripts/run_tests.py tests/ -x -q`

## Risks

- Concurrent Connect updates require refreshed integration tests and preservation of its existing identity/transport contract.
- Pi subprocess/model credentials require an explicit isolated runner; an installed UI is not sandbox evidence.
- Some hosts may be offline; record source-specific verification limits without fabricating successful execution.
