## Tasks

### T001: Implement atomic file-overlap exclusion in claim transaction

**Feature:** F001  
**Priority:** medium  
**Likely files:** src/engine/claim.py, src/engine/transaction.py  

Ensure that the claim transaction uses SQLite row-level locking or appropriate isolation levels so that two concurrent claim attempts for overlapping files can never both succeed. The implementation must be atomic and leave no partial claims.

**Acceptance criteria:**

- Two concurrent claim attempts for overlapping files result in only one success and the other fails with a clear conflict error.
- No partial claims are left in the database after a conflict.

**Verification:**

- `pytest tests/engine/test_claim_overlap.py`
- `python -m src.engine.claim --test-overlap`

---

### T002: Add lease/heartbeat enforcement across code paths

**Feature:** F001  
**Priority:** medium  
**Likely files:** src/engine/lease.py, src/cli/lease.py, src/mcp/lease.py  

Implement lease expiration and heartbeat updates that are honored for both CLI and MCP, accepting fractional minutes without silent loss. The lease must be refreshed by heartbeats and expire correctly when not refreshed.

**Acceptance criteria:**

- A lease expires after the configured duration if no heartbeat is received.
- A heartbeat extends the lease appropriately, even with fractional minute values.

**Verification:**

- `pytest tests/engine/test_lease.py`
- `python -m src.engine.lease --test`

---

### T003: Write concurrency regression test suite for single-winner guarantee

**Feature:** F001  
**Priority:** high  
**Likely files:** tests/engine/test_concurrency.py  
**Dependencies:** T001, T002  

Create a test suite that spawns N threads (e.g., 10) to claim overlapping tasks simultaneously and asserts that only one claim succeeds, proving the single-winner guarantee under real parallelism.

**Acceptance criteria:**

- All tests pass with N=10 threads.
- No race conditions or deadlocks occur during the test run.

**Verification:**

- `pytest tests/engine/test_concurrency.py`

---

### T004: Create init command for new user onboarding

**Feature:** F002  
**Priority:** medium  
**Likely files:** src/cli/init.py  

Implement `anvil init` that sets up the project root, creates the `.anvil` directory, writes a default config file, and initializes an empty PRD. The command should require no external dependencies.

**Acceptance criteria:**

- `anvil init` creates a `.anvil` directory and a `config.yaml` file.
- No external tools are required to run the command.

**Verification:**

- `python -m src.cli.init --test`
- `test -d .anvil && test -f .anvil/config.yaml`

---

### T005: Implement health-diagnosis command

**Feature:** F002  
**Priority:** medium  
**Likely files:** src/cli/health.py  

Provide `anvil health` that reports configuration status, database connectivity, and any missing prerequisites. The output should be JSON for machine consumption.

**Acceptance criteria:**

- The command outputs JSON containing `status`, `config`, and `database` fields.
- It detects and reports a missing `.anvil` directory.

**Verification:**

- `python -m src.cli.health --test`
- `python -m src.cli.health --json | jq .status`

**Dependencies:** T004

---

### T006: Resolve project root consistently across host/container

**Feature:** F003  
**Priority:** medium  
**Likely files:** src/utils/root.py  

Implement logic to determine the project root by walking up from the current directory or using an environment variable, ensuring the same result in both host and container environments.

**Acceptance criteria:**

- `anvil root` returns the same absolute path in host and container.
- The returned path contains the `.anvil` directory.

**Verification:**

- `python -m src.utils.root --test`

---

### T007: Enforce version-pinned command surface

**Feature:** F003  
**Priority:** medium  
**Likely files:** src/cli/main.py  

Ensure that the CLI and MCP expose only commands available for the pinned engine version, rejecting newer commands with a clear error message.

**Acceptance criteria:**

- `anvil --help` lists only supported commands for the current version.
- Attempting an unsupported command returns an error and does not execute.

**Verification:**

- `python -m src.cli.main --help`
- `python -m src.cli.main --unsupported`

**Dependencies:** T006

---

### T008: Audit token footprint

**Feature:** F003  
**Priority:** low  
**Likely files:** src/cli/main.py, src/token/loader.py  

Measure and limit the number of tokens loaded at startup, ensuring it stays within a predefined budget. Log a warning if the budget is exceeded.

**Acceptance criteria:**

- The token count is logged at startup.
- A warning is emitted if the count exceeds the configured budget.

**Verification:**

- `python -m src.cli.main --token-usage`

**Dependencies:** T006

---

### T009: Implement JSON output schema with pagination

**Feature:** F004  
**Priority:** medium  
**Likely files:** src/api/output.py  

Provide `anvil read` that outputs paginated JSON, including a `schema_version` header and supporting `--page` and `--size` arguments.

**Acceptance criteria:**

- Output JSON contains a `schema_version` field.
- Pagination works correctly with `--page` and `--size` options.

**Verification:**

- `python -m src.api.output --read --page 1 --size 10`

---

### T010: Add next ready task naming to completion responses

**Feature:** F004  
**Priority:** medium  
**Likely files:** src/cli/next.py  

Ensure that `anvil next` returns JSON containing a `next_task_id` field that references a ready task.

**Acceptance criteria:**

- The response JSON includes a `next_task_id` key.
- The ID corresponds to a task that is in the ready state.

**Verification:**

- `python -m src.cli.next --json`

**Dependencies:** T009

---

### T011: Implement repo ingestion for brownfield onboarding

**Feature:** F005  
**Priority:** high  
**Likely files:** src/ingest/repo.py  

Add `anvil ingest` that scans an existing repository, creates a draft PRD, and populates the task model with all files and their associated tasks.

**Acceptance criteria:**

- All files in the repository are represented as tasks in the draft PRD.
- The draft PRD is created and stored in `.anvil`.

**Verification:**

- `python -m src.ingest.repo --test`

---

### T012: Support non-feature task types and scoring

**Feature:** F005  
**Priority:** medium  
**Likely files:** src/ingest/repo.py, src/task/scorer.py  

Extend ingestion to classify tasks as bugfix, refactor, or modify, and compute a score for each task based on size and complexity.

**Acceptance criteria:**

- Each task has a `type` field set to one of the supported non-feature types.
- A numeric `score` is assigned to each task.

**Verification:**

- `python -m src.ingest.repo --classify`

**Dependencies:** T011

---

### T013: Create migration script for .anvil artifacts

**Feature:** F006  
**Priority:** high  
**Likely files:** src/migrate/migrate.py  

Provide `anvil migrate` that upgrades on-disk `.anvil` artifacts to the new schema version, preserving all data and updating the schema version stamp.

**Acceptance criteria:**

- Migration completes without data loss.
- The on-disk schema version is updated to the latest.

**Verification:**

- `python -m src.migrate.migrate --dry-run`

---

### T014: Add global-config layer with project overrides

**Feature:** F006  
**Priority:** medium  
**Likely files:** src/config/global.py, src/config/project.py  

Implement a global configuration file that can be overridden by per-project settings, ensuring that project-specific overrides take precedence.

**Acceptance criteria:**

- Global config is loaded at startup.
- Project overrides are applied correctly.

**Verification:**

- `python -m src.config.global --test`

**Dependencies:** T013

---

### T015: Publish Docker MCP catalog entry

**Feature:** F006  
**Priority:** medium  
**Likely files:** Dockerfile, catalog/entry.yaml  

Build the Docker image for the MCP and publish a catalog entry so that the MCP can be installed via the Docker catalog.

**Acceptance criteria:**

- Docker image builds successfully.
- The catalog entry is available and references the correct image.

**Verification:**

- `docker build -t anvil/mcp .`
- `curl -s https://catalog.example.com/anvil/mcp`

**Dependencies:** T014

---

### T016: Query deferred/failed-review evidence

**Feature:** F007  
**Priority:** medium  
**Likely files:** src/query/evidence.py  

Implement an API to retrieve evidence for deferred or failed reviews, returning all relevant data without omission.

**Acceptance criteria:**

- Query returns the correct evidence set for deferred reviews.
- No evidence records are missing in the result.

**Verification:**

- `python -m src.query.evidence --deferred`

---

### T017: Back-propagate decisions to PRD

**Feature:** F007  
**Priority:** high  
**Likely files:** src/review/decision.py  

Ensure that decisions made in review gates are reflected back into the PRD state, updating task status accordingly.

**Acceptance criteria:**

- A decision update changes the corresponding task’s status in the PRD.
- The PRD shows the updated status immediately after the decision.

**Verification:**

- `python -m src.review --decide --task T123`
- `python -m src.prd.view --task T123`

**Dependencies:** T016

---

### T018: Batch dependency edits atomically

**Feature:** F007  
**Priority:** medium  
**Likely files:** src/dependency/batch.py  

Provide an API to edit multiple task dependencies in a single transaction, guaranteeing all-or-nothing semantics.

**Acceptance criteria:**

- All dependency edits succeed together or none are applied.
- No partial updates remain if an error occurs.

**Verification:**

- `python -m src.dependency.batch --edit`

**Dependencies:** T017

---

### T019: Enforce cross-agent contract fields via review gates

**Feature:** F007  
**Priority:** high  
**Likely files:** src/review/gate.py  

Validate that cross-agent contract fields are present and correct before allowing a review to proceed; missing or incorrect fields block the review.

**Acceptance criteria:**

- A review fails with a clear error if required contract fields are missing.
- A review succeeds when all contract fields are present and valid.

**Verification:**

- `python -m src.review --gate`

**Dependencies:** T018

---

### T020: Generate Mermaid diagram from persisted task state

**Feature:** F008  
**Priority:** medium  
**Likely files:** src/diagram/mermaid.py  

Implement `anvil diagram` that outputs Mermaid syntax representing tasks and their dependencies, suitable for rendering in documentation.

**Acceptance criteria:**

- The output is valid Mermaid syntax.
- The diagram accurately reflects the current task state.

**Verification:**

- `python -m src.diagram.mermaid --output diagram.mmd`

---

### T021: Project to GitHub-Issues bidirectional projection

**Feature:** F008  
**Priority:** high  
**Likely files:** src/sync/github.py  

Implement synchronization between local tasks and GitHub issues, supporting creation, updates, and status propagation in both directions.

**Acceptance criteria:**

- A local task creates a corresponding GitHub issue.
- Updates to the GitHub issue propagate back to the local task state.

**Verification:**

- `python -m src.sync.github --test`

**Dependencies:** T020