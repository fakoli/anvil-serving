## Tasks

### T001: Enforce atomic file-overlap exclusion in claim transactions

**Feature:** F001
**Priority:** high
**Likely files:** src/claim/transaction.py, src/claim/concurrency.py
**Dependencies:** T002

<The claim transaction system must enforce that no two tasks overlapping in file access can be simultaneously claimed. This requires a concurrency control mechanism that tracks file access patterns and blocks conflicting claims atomically, with tests validating the behavior under N concurrent threads to ensure single-winner correctness.>

**Acceptance criteria:**

- Two tasks claiming overlapping files cannot both be claimed at the same time
- A concurrency regression suite validates single-winner guarantee under N threads
- The system prevents race conditions in file access during claim operations

**Verification:**

- `python -m pytest tests/test_concurrent_claims.py -v`
- `cargo test --lib claim::concurrent_access`

### T002: Implement fractional lease/heartbeat support across CLI and MCP

**Feature:** F001
**Priority:** medium
**Likely files:** src/lease/manager.py, src/mcp/server.py
**Dependencies:** 

<The lease/heartbeat system must accept fractional minute values without silent truncation or loss, ensuring consistent behavior across all code paths including CLI and MCP interfaces. This includes proper parsing and validation of fractional time inputs and maintaining accurate timing for task expiration.>

**Acceptance criteria:**

- Lease durations can be specified in fractional minutes (e.g., 0.5)
- Fractional values are preserved and not silently truncated
- All code paths (CLI and MCP) honor the configured lease values

**Verification:**

- `python -c "from anvil.lease import LeaseManager; l = LeaseManager(0.5); print(l.duration)"`  
- `pytest tests/test_lease_fractional.py -v`

### T003: Enable standalone onboarding from init to ready task

**Feature:** F002
**Priority:** high
**Likely files:** src/cli/init.py, src/flow/onboarding.py
**Dependencies:** T004

<A new user should be able to run a sequence of commands (init->PRD->plan->next) and reach a ready task without requiring any external crew/flow tools. This involves implementing a self-contained workflow that guides users through setup, PRD creation, planning, and task assignment using only built-in anvil functionality.>

**Acceptance criteria:**

- A new user can execute `anvil init -> PRD -> plan -> next` and reach a ready task
- No external crew/flow dependencies are required
- Self-sufficient documentation is available for the process

**Verification:**

- `anvil init && anvil plan && anvil next` should complete successfully
- `anvil diagnose` should show no missing dependencies

### T004: Resolve project root consistently across host/container environments

**Feature:** F003
**Priority:** high
**Likely files:** src/project/root.py, src/mcp/context.py
**Dependencies:** 

<The engine must resolve the same project root regardless of whether it's running on the host or inside a container, ensuring consistency between CLI and MCP environments. This includes handling path resolution differences and maintaining a stable command surface.>

**Acceptance criteria:**

- Project root resolves identically in CLI and MCP contexts
- Host/container divergence does not affect project identification
- Command surface remains version-pinned and auditable

**Verification:**

- `anvil --project-root` should return same path in container vs host
- `anvil --version` should show pinned version info

### T005: Emit stable, schema-versioned JSON output for read commands

**Feature:** F004
**Priority:** high
**Likely files:** src/read/command.py, src/schema/versioning.py
**Dependencies:** 

<Read commands must emit structured, version-controlled JSON output that includes pagination and schema version information. This ensures compatibility and allows external systems to consume the data reliably while maintaining an authoritative schema version in the on-disk state.>

**Acceptance criteria:**

- Read commands emit paginated, schema-versioned JSON
- On-disk state carries an authoritative schema version
- Completion responses name the next ready task

**Verification:**

- `anvil read --format json` should output valid schema versioned JSON
- `anvil read --paginate 10` should return paginated results

### T006: Ingest existing repo into draft PRD and re-scannable model

**Feature:** F005
**Priority:** high
**Likely files:** src/brownfield/ingest.py, src/code/model.py
**Dependencies:** 

<The engine must be able to scan an existing repository and create a draft PRD along with a re-scannable codebase model. This includes supporting non-feature task types like bugfixes and refactors, and properly sizing the process by score.>

**Acceptance criteria:**

- Engine can ingest an existing repo into a draft PRD
- Codebase model is re-scannable and maintains task types
- Process is right-sized by score for different task categories

**Verification:**

- `anvil brownfield scan /path/to/repo` should create draft PRD
- `anvil list tasks` should show non-feature task types

### T007: Migrate .anvil artifacts across engine versions with global config support

**Feature:** F006
**Priority:** high
**Likely files:** src/migration/engine.py, src/config/global.py
**Dependencies:** 

<On-disk `.anvil` artifacts must migrate cleanly across engine versions, with a global-config layer that merges with project overrides. This ensures upgrade safety and preserves per-project user data during engine updates.>

**Acceptance criteria:**

- Artifacts migrate cleanly across engine versions
- Global-config layer merges with project overrides
- Engine updates do not clobber per-project user data

**Verification:**

- `anvil migrate` should work without data loss
- `anvil config list` should show merged settings

### T008: Query deferred/failed-review evidence and enforce review gates

**Feature:** F007
**Priority:** high
**Likely files:** src/evidence/query.py, src/review/gates.py
**Dependencies:** 

<Deferred or failed review evidence must be queryable and surfaced when there are file overlaps. Decisions must back-propagate to the PRD, and dependency edits must batch atomically. This also includes enforcing cross-agent contract fields via review gates.>

**Acceptance criteria:**

- Deferred/failed review evidence is queryable and visible
- File overlap triggers evidence surfacing
- Decisions back-propagate to the PRD
- Dependency edits batch atomically
- Cross-agent contract fields are enforced by review gates

**Verification:**

- `anvil evidence list` should show failed reviews
- `anvil review validate` should enforce contract fields

### T009: Generate Mermaid diagrams and GitHub-Issues projection from project state

**Feature:** F008
**Priority:** medium
**Likely files:** src/diagram/generator.py, src/github/projection.py
**Dependencies:** 

<Project state must be projectable to auto-generated Mermaid diagrams and optionally to bidirectional GitHub-Issues projections, while maintaining SQLite as the source of truth. This enables legible shared models and external tracker integration.>

**Acceptance criteria:**

- Project state generates auto-generated Mermaid diagrams
- Optional GitHub-Issues projection is supported
- Local SQLite remains source of truth
- Diagrams and projections are consistent with internal state

**Verification:**

- `anvil diagram generate` should produce valid Mermaid output
- `anvil github sync` should update issues if enabled