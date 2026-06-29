## Tasks

### T001: Enforce atomic file-overlap exclusion in the claim transaction

**Feature:** F001
**Priority:** critical
**Likely files:** src/anvil/claim.py, src/anvil/db/schema.py

Guarantee that the claim/lease operation is a single atomic transaction in which two tasks whose declared file sets overlap can never both hold an active claim. The exclusion must be decided inside the same transaction that records the claim (not by a read-then-write check), so the single-winner guarantee holds regardless of interleaving. The implementing agent chooses the locking/constraint mechanism.

**Acceptance criteria:**

- Attempting to claim a task that file-overlaps a currently-claimed task fails with a deterministic, typed rejection rather than producing a second active claim.
- The overlap decision and the claim write occur in one transaction, with no window where two overlapping claims are simultaneously active.
- Unit tests cover claim, conflicting-claim rejection, and release-then-reclaim.

**Verification:**

- `pytest tests/test_claim.py -v`
- `python -m anvil claim --help`

### T002: Build an N-thread single-winner concurrency regression suite

**Feature:** F001
**Priority:** critical
**Likely files:** tests/test_concurrency.py, tests/conftest.py
**Dependencies:** T001

Add a standing concurrency regression suite that launches N concurrent workers all racing to claim the same file-overlapping task set and asserts that exactly one winner is recorded per conflict group across many repetitions. The suite must be deterministic in its assertions (counts), seedable, and fast enough to run in CI.

**Acceptance criteria:**

- A parameterized test runs at least N=16 concurrent claimers over repeated trials and asserts exactly one active claim per conflict group every trial.
- The suite fails if file-overlap exclusion is disabled or made non-atomic (proven by a deliberately-broken local run).
- The suite is wired into the default test run and completes without hanging.

**Verification:**

- `pytest tests/test_concurrency.py -v`
- `pytest tests/test_concurrency.py -k single_winner --count=5`

### T003: Honor configured lease/heartbeat values on every code path including fractional minutes

**Feature:** F001
**Priority:** high
**Likely files:** src/anvil/lease.py, src/anvil/cli.py, src/anvil/mcp/server.py

Ensure the configured lease duration and heartbeat interval are read and applied identically on both the CLI and MCP code paths, and that fractional-minute values are parsed and stored without silent truncation or loss. A lease set on one surface must be observed the same way on the other.

**Acceptance criteria:**

- A fractional lease value (e.g. 1.5 minutes) round-trips through config to the stored expiry without rounding to an integer.
- CLI and MCP claim paths produce the same lease expiry for the same configured value.
- Tests cover fractional parsing, expiry computation, and heartbeat renewal extending the lease.

**Verification:**

- `pytest tests/test_lease.py -v`
- `python -m anvil config get lease_minutes`

### T004: Deliver a standalone init->PRD->plan->next path with zero crew/flow dependency

**Feature:** F002
**Priority:** high
**Likely files:** src/anvil/cli.py, src/anvil/onboarding.py

Make a brand-new user able to run init, author/import a PRD, plan, and reach a ready task end-to-end without any crew or flow package installed. The full first-run loop must function with only the core engine present.

**Acceptance criteria:**

- In an environment with no crew/flow packages installed, running init then plan then next yields a ready task.
- No import of crew/flow occurs on the init->plan->next path (verified by import audit or by running under an environment that lacks them).
- An end-to-end test exercises the four-step path against a temp project and asserts a ready task is returned.

**Verification:**

- `pytest tests/test_onboarding.py -v`
- `python -m anvil next --help`

### T005: Add a health-diagnosis command

**Feature:** F002
**Priority:** medium
**Likely files:** src/anvil/cli.py, src/anvil/health.py

Provide a single command that diagnoses first-run health: project root resolution, database presence and schema version, config validity, and whether a ready task is reachable. It must report each check with a pass/fail status and a non-zero exit code when any critical check fails.

**Acceptance criteria:**

- The command emits one line per check with an explicit pass/fail marker.
- The command exits non-zero when a critical check (e.g. missing/corrupt database) fails and zero when all pass.
- Tests cover a healthy project and at least one degraded project.

**Verification:**

- `pytest tests/test_health.py -v`
- `python -m anvil doctor --help`

### T006: Write self-sufficient standalone onboarding docs

**Feature:** F002
**Priority:** medium
**Likely files:** docs/onboarding.md, docs/quickstart.md
**Dependencies:** T004, T005

Author onboarding documentation that walks a new user through the standalone init->PRD->plan->next loop and the health-diagnosis command with no reference to crew/flow. The docs must contain only commands that actually exist and succeed.

**Acceptance criteria:**

- Every shell command shown in the quickstart executes successfully against a fresh temp project.
- The docs reference the health-diagnosis command and the standalone loop and contain no crew/flow installation steps.
- A docs-commands test extracts and runs the fenced commands and asserts they exit zero.

**Verification:**

- `pytest tests/test_docs_commands.py -v`
- `python -m anvil doctor`

### T007: Unify project-root resolution across host and container for CLI and MCP

**Feature:** F003
**Priority:** high
**Likely files:** src/anvil/root.py, src/anvil/cli.py, src/anvil/mcp/server.py

Make the CLI and the MCP server resolve to the identical project root even when paths differ between host and container (bind-mount divergence). Root resolution logic must be shared by both surfaces so they cannot disagree.

**Acceptance criteria:**

- Given the same project, CLI and MCP report byte-identical resolved project-root paths under a simulated host/container path divergence.
- Root resolution lives in one shared function used by both surfaces (no duplicated resolver).
- Tests cover host path, container-mapped path, and an ambiguous/no-root error case.

**Verification:**

- `pytest tests/test_root.py -v`
- `python -m anvil root`

### T008: Self-describe a version-pinned command surface

**Feature:** F003
**Priority:** medium
**Likely files:** src/anvil/surface.py, src/anvil/mcp/server.py

Expose a machine-readable self-description of the available commands pinned to an explicit surface version, so an MCP/ACP host can discover the exact command set and its version. The descriptor must change version when the command surface changes.

**Acceptance criteria:**

- A self-describe call returns a list of commands and an explicit surface-version field.
- The descriptor validates against a fixed schema and is stable for an unchanged surface.
- A test asserts every CLI subcommand appears in the descriptor.

**Verification:**

- `pytest tests/test_surface.py -v`
- `python -m anvil describe --json`

### T009: Audit and bound the always-loaded plugin token footprint

**Feature:** F003
**Priority:** medium
**Likely files:** tests/test_token_budget.py, src/anvil/plugin/manifest.py

Measure the always-loaded token footprint of the plugin surface (the text injected on every load) and enforce an audited upper budget so the footprint cannot silently grow past an agreed ceiling.

**Acceptance criteria:**

- A test computes the always-loaded token count and asserts it is at or below a defined budget constant.
- The budget constant and the measured value are reported when the test fails.
- The audit covers all always-loaded plugin text, not a single file.

**Verification:**

- `pytest tests/test_token_budget.py -v`
- `python -m anvil plugin tokens`

### T010: Add an authoritative on-disk schema version to state

**Feature:** F004
**Priority:** medium
**Likely files:** src/anvil/db/schema.py, src/anvil/db/version.py

Persist an authoritative schema version inside the on-disk state so any reader can determine the exact schema the data was written under. The engine must read this version on open and refuse or migrate mismatches rather than guessing.

**Acceptance criteria:**

- Opening a database exposes a single authoritative schema-version value read from the store.
- A freshly initialized database records the current schema version.
- Tests cover reading the version and detecting a version mismatch.

**Verification:**

- `pytest tests/test_schema_version.py -v`
- `python -m anvil db version`

### T011: Emit stable, schema-versioned, paginated JSON from read commands

**Feature:** F004
**Priority:** high
**Likely files:** src/anvil/output.py, src/anvil/cli.py
**Dependencies:** T010

Make every read command able to emit JSON that carries an explicit output schema version and supports pagination (page/limit with a stable ordering and a continuation token or offset). The JSON shape must be stable across runs for the same data so scripts and hosts can depend on it.

**Acceptance criteria:**

- Read commands emit JSON containing a schema-version field and pagination metadata.
- Paginating through results returns every record exactly once under a stable ordering.
- JSON output validates against a committed schema fixture.

**Verification:**

- `pytest tests/test_json_output.py -v`
- `python -m anvil tasks list --json --limit 2`

### T012: Name the next ready task in completion responses

**Feature:** F004
**Priority:** medium
**Likely files:** src/anvil/complete.py, src/anvil/cli.py

When a task is completed, the response must name the next ready task (if any) so an agent can continue without a separate query. The chosen next task must respect dependency and conflict ordering.

**Acceptance criteria:**

- Completing a task returns a response that includes the id of the next ready task, or an explicit none-available marker.
- The named next task is actually claimable (passes dependency and conflict checks) at the time it is returned.
- Tests cover completion with a next task available and with none available.

**Verification:**

- `pytest tests/test_complete.py -v`
- `python -m anvil complete --help`

### T013: Ingest an existing repo into a draft PRD and a re-scannable codebase model

**Feature:** F005
**Priority:** high
**Likely files:** src/anvil/scan.py, src/anvil/ingest.py

Open a brownfield front door: scan an existing repository and produce a draft PRD plus a persisted, re-scannable codebase model that later runs can refresh. The scan must be idempotent so re-running updates the model rather than duplicating it. (May need expand.)

**Acceptance criteria:**

- Running the scan on a sample repo produces a draft PRD artifact and a persisted codebase model.
- Re-running the scan updates the existing model without creating duplicate entries.
- Tests cover initial ingest and a re-scan that reflects an added file.

**Verification:**

- `pytest tests/test_scan.py -v`
- `python -m anvil scan --help`

### T014: Carry non-feature task types through the full loop with score-based right-sizing

**Feature:** F005
**Priority:** high
**Likely files:** src/anvil/tasks.py, src/anvil/scoring.py

Support bugfix, refactor, and modify task types end-to-end (plan->claim->complete), and right-size the process applied to each task by its score so small changes do not incur full-feature ceremony.

**Acceptance criteria:**

- A bugfix, a refactor, and a modify task each traverse the full plan->claim->complete loop in tests.
- Tasks below a score threshold receive a reduced process path and tasks above it receive the full path, asserted by the right-sizing logic.
- Task type is persisted and surfaced in read output.

**Verification:**

- `pytest tests/test_task_types.py -v`
- `python -m anvil tasks list --json`

### T015: Migrate .anvil artifacts cleanly across engine versions

**Feature:** F006
**Priority:** high
**Likely files:** src/anvil/db/migrations.py, src/anvil/migrate.py
**Dependencies:** T010

Provide an upgrade-safe migration that moves on-disk `.anvil` artifacts from an older schema version to the current one without clobbering user data. Migration must be transactional and idempotent so an interrupted run can be safely retried.

**Acceptance criteria:**

- Migrating a fixture project from the previous schema version succeeds and preserves all pre-existing rows.
- Re-running the migration on an already-current store is a no-op.
- A migration test asserts data equivalence (counts and key fields) before and after.

**Verification:**

- `pytest tests/test_migrations.py -v`
- `python -m anvil migrate --help`

### T016: Merge a global-config layer under project overrides

**Feature:** F006
**Priority:** medium
**Likely files:** src/anvil/config.py, src/anvil/config_layers.py

Add a global (user-level) configuration layer that supplies defaults which any project-level configuration overrides. Resolution order must be deterministic and inspectable so a user can see which layer supplied each effective value.

**Acceptance criteria:**

- A value set only globally is used when the project does not set it; a project value overrides the global one.
- The effective config can be printed showing the source layer for each key.
- Tests cover global-only, project-only, and override cases.

**Verification:**

- `pytest tests/test_config_layers.py -v`
- `python -m anvil config show --sources`

### T017: Package for Docker MCP catalog installation

**Feature:** F006
**Priority:** medium
**Likely files:** Dockerfile, mcp-catalog.json, docs/install-docker.md

Make the engine installable via the Docker MCP catalog by providing the container image definition and the catalog manifest entry needed for discovery and install. The image must start the MCP server with a resolvable project root.

**Acceptance criteria:**

- The image builds and the catalog manifest validates against the catalog schema.
- The built container starts the MCP server and responds to a self-describe/health probe.
- A test or scripted check asserts the manifest fields required by the catalog are present.

**Verification:**

- `docker build -t anvil-mcp .`
- `pytest tests/test_catalog_manifest.py -v`

### T018: Make deferred and failed-review evidence queryable and surface it on file overlap

**Feature:** F007
**Priority:** high
**Likely files:** src/anvil/evidence.py, src/anvil/claim.py

Persist deferred and failed-review evidence so it can be queried, and surface relevant prior evidence when a task overlaps files with work that has such evidence, so an agent sees known problems before claiming.

**Acceptance criteria:**

- Deferred and failed-review evidence can be queried by task and by file.
- Claiming or inspecting a task that file-overlaps prior failed/deferred evidence surfaces that evidence in the response.
- Tests cover querying evidence and the file-overlap surfacing path.

**Verification:**

- `pytest tests/test_evidence.py -v`
- `python -m anvil evidence list --help`

### T019: Back-propagate decisions to the PRD

**Feature:** F007
**Priority:** high
**Likely files:** src/anvil/decisions.py, src/anvil/prd.py

When a decision is recorded during execution, propagate it back into the originating PRD as a durable, additive transition so the PRD reflects what was actually decided rather than drifting from reality.

**Acceptance criteria:**

- Recording a decision creates an additive link from the decision to the affected PRD element.
- The PRD view reflects the back-propagated decision without overwriting prior content.
- Tests cover recording a decision and reading it back from the PRD projection.

**Verification:**

- `pytest tests/test_decisions.py -v`
- `python -m anvil decision record --help`

### T020: Batch dependency edits atomically and enforce cross-agent contract fields via review gates

**Feature:** F007
**Priority:** high
**Likely files:** src/anvil/dependencies.py, src/anvil/review_gate.py

Allow a set of dependency edits to be applied as one atomic batch (all-or-nothing, no partial graph), and enforce required cross-agent contract fields at review gates so a task cannot pass review with missing handoff contract data.

**Acceptance criteria:**

- A batch of dependency edits either fully applies or fully rolls back on any failure, leaving no partial state and introducing no cycle.
- A review gate rejects completion when a required cross-agent contract field is missing and passes when present.
- Tests cover a successful batch, a rolled-back batch, and a gate rejection.

**Verification:**

- `pytest tests/test_dependencies.py tests/test_review_gate.py -v`
- `python -m anvil deps edit --help`

### T021: Project task state to an auto-generated Mermaid diagram

**Feature:** F008
**Priority:** medium
**Likely files:** src/anvil/projection/mermaid.py, src/anvil/cli.py

Generate a Mermaid diagram from persisted task state (tasks, dependencies, statuses) directly from the SQLite source of truth, so the graph is always derived rather than hand-maintained.

**Acceptance criteria:**

- The command emits valid Mermaid text representing tasks, their dependency edges, and statuses.
- The diagram is regenerated from current state on each run with no manual editing required.
- A test asserts the output parses as a valid Mermaid graph and contains all task ids.

**Verification:**

- `pytest tests/test_mermaid.py -v`
- `python -m anvil diagram --format mermaid`

### T022: Project task state to an opt-in bidirectional GitHub Issues sync

**Feature:** F008
**Priority:** medium
**Likely files:** src/anvil/projection/github.py, src/anvil/sync.py

Provide an opt-in bidirectional projection between task state and GitHub Issues while keeping local SQLite as the authoritative source of truth, so changes flow both ways without the projection becoming canonical.

**Acceptance criteria:**

- With the projection disabled, no GitHub calls are made on any path.
- When enabled, local task changes map to issue updates and inbound issue changes map back to local state, with SQLite remaining authoritative on conflict.
- Tests cover the disabled default, an outbound projection, and an inbound reconciliation using a mocked GitHub client.

**Verification:**

- `pytest tests/test_github_projection.py -v`
- `python -m anvil sync github --help`
