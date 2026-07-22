## Tasks

### T001: Add the prds table with identity and release fields

**Feature:** F001
**Priority:** high
**Likely files:** src/anvil/db/schema.py, src/anvil/models/prd.py

Introduce a `prds` table that holds many PRD rows, each with a stable identity (`PRD.id`), a status, and release fields (`target_version`, `target_tag`), with an invariant of exactly one `is_default` PRD per project. This is the model/schema foundation only; no behavior changes elsewhere yet.

**Acceptance criteria:**

- The `prds` table stores multiple rows keyed by `id` with status and `target_version`/`target_tag` columns.
- Exactly one row may have `is_default` true per project, enforced at write time.
- Tests cover inserting multiple PRDs and rejecting a second default.

**Verification:**

- `pytest tests/test_prd_model.py -v`
- `python -c "import anvil.models.prd"`

### T002: Add the prd_id partition column to Requirement, Feature, and Task with the ownership invariant

**Feature:** F001
**Priority:** high
**Likely files:** src/anvil/db/schema.py, src/anvil/models/task.py
**Dependencies:** T001

Add an explicit `prd_id` partition column to Requirement, Feature, and Task (denormalized onto Task), and enforce the invariant `Task.prd_id == owning Feature.prd_id` at write time so a task can never be attributed to a different PRD than its feature.

**Acceptance criteria:**

- Requirement, Feature, and Task rows each carry a non-null `prd_id` referencing a `prds` row.
- A write where `Task.prd_id` differs from its owning Feature's `prd_id` is rejected.
- Tests cover a valid write and a violating write.

**Verification:**

- `pytest tests/test_partition_columns.py -v`
- `python -c "import anvil.models.task"`

### T003: De-literalize the schema-version gate into an ordered migration ladder

**Feature:** F002
**Priority:** high
**Likely files:** src/anvil/db/migrations.py, src/anvil/db/version.py

Replace the hardcoded `SCHEMA_VERSION == 6` literal gate with an ordered migration ladder that applies registered steps in sequence from the stored version to the current one. This makes future bumps additive and is prerequisite plumbing for the v6->v7 step.

**Acceptance criteria:**

- The migration system applies an ordered list of steps from the stored version to the target with no literal-equality gate remaining.
- A store already at the target version triggers no steps.
- Tests cover laddering across at least one step and a no-op at the current version.

**Verification:**

- `pytest tests/test_migration_ladder.py -v`
- `grep -rn "SCHEMA_VERSION == 6" src/anvil || echo "no literal gate"`

### T004: Implement the v6->v7 migration backfilling a default PRD with zero data loss

**Feature:** F002
**Priority:** critical
**Likely files:** src/anvil/db/migrations.py, src/anvil/db/migrations/v7_default_prd.py
**Dependencies:** T001, T002, T003

Add the single v6->v7 migration step that creates the `prds` table, backfills one `default` PRD, and assigns it as `prd_id` owner of every existing Requirement/Feature/Task row, with zero data loss. Because SQLite cannot ALTER a PRIMARY KEY, the `prds` rebuild (CREATE/INSERT-SELECT/DROP/RENAME) must run inside a SAVEPOINT atomically and be crash-idempotent.

**Acceptance criteria:**

- Migrating a v6 fixture to v7 yields one `default` PRD owning every pre-existing row with identical row counts and field values.
- The migration runs inside a single atomic transaction/SAVEPOINT; an injected mid-migration failure rolls back leaving a valid v6 store, and a re-run completes successfully (crash-idempotent).
- The stored schema version reads 7 after success.

**Verification:**

- `pytest tests/test_v7_migration.py -v`
- `python -m anvil migrate --to 7 --help`

### T005: Build the replay-equivalence oracle for pre-v7 logs and direct multi-PRD DBs

**Feature:** F002
**Priority:** high
**Likely files:** tests/test_replay_equivalence.py, src/anvil/replay.py
**Dependencies:** T004

Add a replay-equivalence oracle proving that replaying a pre-v7 event log from empty reconstructs the `default`-owned state byte-identically, and that a directly-built multi-PRD database replays byte-identically to its source. This pins replay correctness across the migration boundary.

**Acceptance criteria:**

- Replaying a pre-v7 log from empty produces state byte-identical to the migrated v7 state for the same input.
- A directly-built multi-PRD database serializes and replays to a byte-identical result.
- The oracle fails if serialization ordering is made nondeterministic (proven by a local perturbation).

**Verification:**

- `pytest tests/test_replay_equivalence.py -v`
- `pytest tests/test_replay_equivalence.py -k byte_identical`

### T006: Expose the backend partition API and prd_id filters

**Feature:** F003
**Priority:** high
**Likely files:** src/anvil/backend.py, src/anvil/store.py
**Dependencies:** T001, T002

Add the backend partition surface: `get_prd(prd_id)`, `list_prds()`, `default_prd_id()`, and `prd_id` filter arguments on `list_tasks`, `list_features`, and `list_requirements`. These are the read primitives every later phase builds on.

**Acceptance criteria:**

- `get_prd`, `list_prds`, and `default_prd_id` return correct results over a multi-PRD store.
- `list_tasks`/`list_features`/`list_requirements` accept a `prd_id` filter and return only that PRD's rows when supplied.
- Tests cover each accessor and each filter.

**Verification:**

- `pytest tests/test_partition_api.py -v`
- `python -c "from anvil.backend import get_prd, list_prds, default_prd_id"`

### T007: Resolve the 12 legacy no-arg get_prd() call sites to the default PRD

**Feature:** F003
**Priority:** high
**Likely files:** src/anvil/backend.py, src/anvil/cli.py, src/anvil/mcp/server.py
**Dependencies:** T006

Audit and update the 12 legacy no-arg `get_prd()` call sites so each explicitly resolves to the default PRD, closing the correctness trap where an unaudited call would silently operate on the default PRD once multiple exist.

**Acceptance criteria:**

- All 12 legacy no-arg `get_prd()` sites are updated to resolve the PRD explicitly via the default resolver.
- A repository check confirms no remaining bare no-arg `get_prd()` calls outside the resolver itself.
- Tests assert the audited paths operate on the default PRD in a multi-PRD store.

**Verification:**

- `pytest tests/test_get_prd_audit.py -v`
- `grep -rn "get_prd()" src/anvil | grep -v default_prd_id || echo "all audited"`

### T008: Pin cross-PRD coordination with regression tests before any --prd narrowing

**Feature:** F004
**Priority:** critical
**Likely files:** tests/test_cross_prd_coordination.py, src/anvil/claim.py
**Dependencies:** T002

Land regression tests pinning that conflict groups, active-claim exclusion, and the stale reaper span ALL PRDs, BEFORE any `--prd` narrowing is introduced. This guards the cross-PRD moat: two tasks in different PRDs touching the same file must still conflict, and the reaper must reclaim across PRDs.

**Acceptance criteria:**

- A test proves two tasks in different PRDs that touch the same file cannot both hold active claims.
- A test proves the stale reaper reclaims expired leases regardless of owning PRD.
- These tests are committed and passing before any task introduces `--prd` exclusion narrowing.

**Verification:**

- `pytest tests/test_cross_prd_coordination.py -v`
- `pytest tests/test_cross_prd_coordination.py -k cross_prd_conflict`

### T009: Make the claim gate check the task's owning PRD and collapse the MCP gate onto ClaimManager

**Feature:** F004
**Priority:** critical
**Likely files:** src/anvil/claim.py, src/anvil/mcp/server.py
**Dependencies:** T006, T008

Make the claim gate resolve the task's owning PRD via `get_prd_for_task` and check that PRD's status: an approved PRD is claimable, a draft PRD is not. Collapse the duplicated MCP-side gate onto the single `ClaimManager` so CLI and MCP enforce identical rules.

**Acceptance criteria:**

- Claiming a task whose owning PRD is draft is rejected; claiming one whose owning PRD is approved succeeds (other gates permitting).
- The MCP claim path delegates to the same `ClaimManager` as the CLI with no duplicated gate logic.
- The cross-PRD coordination regression suite still passes after this change.

**Verification:**

- `pytest tests/test_claim_gate_prd.py tests/test_cross_prd_coordination.py -v`
- `python -m anvil claim --help`

### T010: Make the parser prd_id load-bearing with bare default and prefixed named ids

**Feature:** F005
**Priority:** high
**Likely files:** src/anvil/parser.py, src/anvil/ids.py
**Dependencies:** T006

Make the parser `prd_id`-load-bearing: the default PRD emits BARE ids (`T001`) while named PRDs emit PREFIXED ids (`v0.2:T001`), and `prd parse --prd <id>` reads `.anvil/prds/<id>.md` and touches only that PRD's rows. Keeping default ids bare limits blast radius on any `^T\d+` matcher in claims/skills/drift.

**Acceptance criteria:**

- Parsing the default PRD produces bare ids (`T001`); parsing a named PRD produces prefixed ids (`v0.2:T001`).
- `prd parse --prd <id>` reads `.anvil/prds/<id>.md` and writes/updates only that PRD's rows, leaving other PRDs untouched.
- Tests cover bare-id emission, prefixed-id emission, and scoped write isolation.

**Verification:**

- `pytest tests/test_parser_prd_id.py -v`
- `python -m anvil prd parse --prd v0.2 --help`

### T011: Scope plan --prd to that PRD's orphans while inferring conflicts across all PRDs

**Feature:** F005
**Priority:** high
**Likely files:** src/anvil/plan.py, src/anvil/conflict.py
**Dependencies:** T008, T010

Make `plan --prd` prune only the orphans belonging to that PRD, while conflict-group inference continues to read ALL PRDs' tasks. A naive `list_tasks(prd_id=)` for the exclusion sets would silently break cross-PRD conflict detection, so the conflict path must stay global.

**Acceptance criteria:**

- `plan --prd X` removes only PRD X's orphaned tasks and leaves other PRDs' tasks intact.
- Conflict-group inference during plan reads tasks from all PRDs, so a cross-PRD file overlap is still detected.
- Tests cover scoped orphan pruning and cross-PRD conflict detection during a scoped plan.

**Verification:**

- `pytest tests/test_plan_prd_scope.py -v`
- `python -m anvil plan --prd v0.2 --help`

### T012: Implement the shared resolve_prd_id helper across CLI and MCP

**Feature:** F006
**Priority:** high
**Likely files:** src/anvil/resolve.py, src/anvil/cli.py, src/anvil/mcp/server.py
**Dependencies:** T006

Add a shared `resolve_prd_id` helper with the precedence explicit `--prd` > `ANVIL_PRD` env > single/default, raising an ambiguity error when multiple PRDs exist and none is selected. Thread it through both CLI and MCP so resolution is identical; read-only rollups default to all PRDs.

**Acceptance criteria:**

- Resolution follows `--prd` > `ANVIL_PRD` > single|default and raises a typed ambiguity error when no selector is given and multiple non-default PRDs are eligible.
- CLI and MCP produce identical resolution for identical inputs.
- Read-only rollup paths default to all PRDs rather than erroring on ambiguity.

**Verification:**

- `pytest tests/test_resolve_prd_id.py -v`
- `ANVIL_PRD=v0.2 python -m anvil tasks list --help`

### T013: Add a per-PRD plus project-total status rollup

**Feature:** F006
**Priority:** medium
**Likely files:** src/anvil/status.py, src/anvil/cli.py
**Dependencies:** T006, T012

Make `anvil status` roll up counts per PRD and also report a project total across all PRDs, so a user sees each release-scoped plan and the whole project at once.

**Acceptance criteria:**

- `anvil status` shows a per-PRD breakdown and a project-total line whose totals equal the sum of the per-PRD rows.
- With a single/default PRD only, output remains equivalent to prior single-PRD behavior.
- Tests cover a multi-PRD rollup and the single-PRD case.

**Verification:**

- `pytest tests/test_status_rollup.py -v`
- `python -m anvil status --json`

### T014: Emit prd.revised with non-destructive supersede and a per-PRD revision counter

**Feature:** F007
**Priority:** high
**Likely files:** src/anvil/events.py, src/anvil/revision.py
**Dependencies:** T006

Make re-parse of an existing PRD emit a `prd.revised` event that non-destructively supersedes prior content via revision-lineage columns plus a per-PRD revision counter, rather than deleting and rewriting rows. The audit log stays unified across all PRDs.

**Acceptance criteria:**

- Re-parsing an existing PRD emits `prd.revised`, increments that PRD's revision counter, and preserves superseded rows via lineage columns (no destructive delete).
- The per-PRD counter is independent across PRDs and the unified event log records the revision.
- Tests cover a first parse, a re-parse supersede, and counter independence between two PRDs.

**Verification:**

- `pytest tests/test_prd_revision.py -v`
- `python -m anvil prd parse --prd v0.2`

### T015: Make serialize_state enumerate all PRDs deterministically and support replay-to-revision

**Feature:** F007
**Priority:** high
**Likely files:** src/anvil/replay.py, src/anvil/serialize.py
**Dependencies:** T014

Make `serialize_state` enumerate all PRDs in a deterministic order and add `replay_to_event_id` so state can be reconstructed as-of a given revision. This completes event-sourced revision and keeps replay output stable.

**Acceptance criteria:**

- `serialize_state` produces byte-identical output across runs for the same multi-PRD store (deterministic PRD enumeration).
- `replay_to_event_id` reconstructs state as-of a specified revision, matching the historical state at that point.
- Tests cover deterministic serialization and as-of replay to a prior revision.

**Verification:**

- `pytest tests/test_replay_to_revision.py -v`
- `pytest tests/test_replay_to_revision.py -k deterministic`

### T016: Plumb release/sync data through SyncMapping with per-PRD push and reconciliation

**Feature:** F008
**Priority:** medium
**Likely files:** src/anvil/sync.py, src/anvil/models/sync_mapping.py
**Dependencies:** T001, T012

Add release/sync data plumbing: `SyncMapping` gains `prd_id` and `entity_kind`; push stamps the owning `prd_id`; `--prd` scopes push; and reconciliation attributes discrepancies to a PRD. Network-touching milestone creation is deferred, so only the data plumbing lands here.

**Acceptance criteria:**

- `SyncMapping` rows carry `prd_id` and `entity_kind`, and push stamps the owning `prd_id` on each mapping.
- `push --prd X` scopes the push to PRD X's entities and reconciliation reports each discrepancy attributed to a PRD.
- No network milestone-creation calls are made; tests use a mocked transport.

**Verification:**

- `pytest tests/test_sync_mapping_prd.py -v`
- `python -m anvil push --prd v0.2 --help`

### T017: Complete the v7 version-lockstep, packaging manifest, and version-doc refresh

**Feature:** F009
**Priority:** high
**Likely files:** pyproject.toml, src/anvil/__init__.py, docs/versions.md
**Dependencies:** T004

The v7 bump is publishable, so complete the full version-lockstep across code constants and packaging manifests and refresh the user-facing version documentation, so engine version, schema version, and package metadata all agree before publish.

**Acceptance criteria:**

- The package version, the code schema-version constant (7), and the packaging manifest all report the v7-consistent values.
- A version-consistency check passes asserting the three sources agree.
- The user-facing version doc references the v7 schema and migration.

**Verification:**

- `pytest tests/test_version_lockstep.py -v`
- `python -m anvil db version`

### T018: Reframe skills, docs, and positioning around PRD as a release-scoped revisable plan

**Feature:** F009
**Priority:** medium
**Likely files:** docs/prd-model.md, skills/anvil/SKILL.md
**Dependencies:** T017

Reframe the skills, docs, and positioning so a PRD is described as a release/milestone-scoped, separately-gated, revisable plan, and so every documented command reflects the multi-PRD `--prd` surface. Documented commands must actually exist and succeed.

**Acceptance criteria:**

- Skills and docs describe the PRD as release-scoped, separately-gated, and revisable, and reference the `--prd` selector.
- Every shell command shown in the reframed docs executes successfully against a fresh multi-PRD project.
- A docs-commands check extracts and runs the fenced commands and asserts they exit zero.

**Verification:**

- `pytest tests/test_docs_commands.py -v`
- `python -m anvil prd parse --help`
