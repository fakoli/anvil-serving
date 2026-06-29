## Tasks

### T001: Implement PRD schema and migration from v6 to v7

**Feature:** F002
**Priority:** high
**Likely files:** src/schema/v7.py, src/migration/v6_to_v7.py
**Dependencies:** T002

<This task implements the v6->v7 migration that backfills a 'default' PRD to own all existing rows. It includes creating new tables for `prds`, `requirements`, `features`, and `tasks` with `prd_id` columns, and ensuring zero data loss during the atomic transaction.>

**Acceptance criteria:**

- The migration correctly identifies and migrates all existing rows into a single default PRD.
- All existing data is preserved in the new schema without loss or corruption.
- The `SCHEMA_VERSION` is updated to 7 and the migration is idempotent.

**Verification:**

- `anvil migrate --to 7` runs successfully on a v6 database.
- `anvil status` shows the migrated state with a single default PRD.

### T002: Add PRD table and related fields to schema

**Feature:** F001
**Priority:** high
**Likely files:** src/schema/prd.py, src/schema/tables.py
**Dependencies:** 

<This task adds the `prds` table with `id`, `target_version`, `target_tag`, and `is_default` fields. It also ensures that `requirements`, `features`, and `tasks` have an explicit `prd_id` column.>

**Acceptance criteria:**

- The `prds` table exists with unique `id` and exactly one `is_default`.
- The `requirements`, `features`, and `tasks` tables include `prd_id` columns.
- The `prd_id` field is properly indexed for performance.

**Verification:**

- `sqlite3 state.db "PRAGMA table_info(prds);"` shows the correct schema.
- `sqlite3 state.db "PRAGMA table_info(requirements);"` shows `prd_id`.

### T003: Implement backend API for PRD management

**Feature:** F003
**Priority:** high
**Likely files:** src/backend/prd_api.py, src/models/prd.py
**Dependencies:** T002

<This task implements the backend functions to manage PRDs including `get_prd(prd_id)`, `list_prds()`, `default_prd_id()`, and filters on `list_tasks`, `list_features`, and `list_requirements`.>

**Acceptance criteria:**

- The `get_prd(prd_id)` function returns the correct PRD object.
- The `list_prds()` function returns all PRDs.
- The `default_prd_id()` function returns the ID of the default PRD.
- The list functions accept a `prd_id` filter and return only matching entities.

**Verification:**

- `python -m anvil.backend get_prd default` returns the default PRD.
- `python -m anvil.backend list_prds` returns all PRDs.
- `python -m anvil.backend list_tasks prd_id=default` returns only tasks from the default PRD.

### T004: Implement claim gate logic for PRD-based access control

**Feature:** F004
**Priority:** high
**Likely files:** src/claim/gate.py, src/models/task.py
**Dependencies:** T003

<This task implements the claim gate logic that checks the task's owning PRD’s status before allowing claims. It ensures that approved PRDs are claimable while draft PRDs are not.>

**Acceptance criteria:**

- The claim gate resolves the task's owning PRD.
- Claims are allowed only if the PRD is approved.
- Claims are denied if the PRD is draft or pending.

**Verification:**

- `anvil claim T001` works when the owning PRD is approved.
- `anvil claim T001` fails when the owning PRD is draft.

### T005: Enable cross-PRD conflict detection and coordination

**Feature:** F004
**Priority:** high
**Likely files:** src/conflict/detector.py, src/reaper/stale.py
**Dependencies:** T004

<This task ensures that conflict groups, active-claim exclusion, and the stale reaper span all PRDs. It maintains the integrity of cross-PRD dependencies and ensures no silent breakage of the moat.>

**Acceptance criteria:**

- Conflict detection spans all PRDs.
- Active claims exclude across PRDs.
- Stale reaper operates across all PRDs.

**Verification:**

- `anvil plan` detects conflicts across PRDs.
- `anvil reaper` cleans up stale claims across PRDs.

### T006: Implement parser support for PRD-specific parsing and ID handling

**Feature:** F005
**Priority:** high
**Likely files:** src/parser/prd.py, src/parser/id_resolver.py
**Dependencies:** T003

<This task makes the parser load-bearing for PRDs by supporting `prd parse --prd <id>` and emitting prefixed TaskIDs for named PRDs. It ensures that `.anvil/prds/<id>.md` is read and only that PRD's rows are touched.>

**Acceptance criteria:**

- `anvil parse --prd <id>` reads from `.anvil/prds/<id>.md`.
- Prefixed TaskIDs (`v0.2:T001`) are emitted for named PRDs.
- Bare IDs (`T001`) are emitted for the default PRD.

**Verification:**

- `anvil parse --prd v0.2` reads from `.anvil/prds/v0.2.md`.
- `anvil parse --prd v0.2` emits prefixed IDs like `v0.2:T001`.

### T007: Implement CLI and MCP support for --prd selector

**Feature:** F006
**Priority:** high
**Likely files:** src/cli/main.py, src/mcp/handlers.py
**Dependencies:** T006

<This task adds a `--prd` option to CLI and MCP commands that defaults to the single/default PRD. It ensures that `resolve_prd_id` handles explicit `--prd`, `ANVIL_PRD`, and default resolution.>

**Acceptance criteria:**

- `anvil plan --prd <id>` prunes only that PRD's orphans.
- `anvil status --prd <id>` rolls up per-PRD stats.
- `resolve_prd_id` correctly resolves `--prd`, `ANVIL_PRD`, and default.

**Verification:**

- `anvil plan --prd v0.2` filters to only tasks in that PRD.
- `ANVIL_PRD=v0.2 anvil status` uses the specified PRD.

### T008: Implement event-sourced revision system for PRDs

**Feature:** F007
**Priority:** high
**Likely files:** src/replay/event_source.py, src/models/prd.py
**Dependencies:** T003

<This task implements non-destructive supersede via revision-lineage columns and a per-PRD revision counter. It allows `replay_to_event_id` to reconstruct as-of a revision and ensures deterministic enumeration of all PRDs.>

**Acceptance criteria:**

- A `prd.revised` column tracks revisions.
- A per-PRD revision counter is maintained.
- `replay_to_event_id` reconstructs as-of a revision.
- `serialize_state` enumerates all PRDs deterministically.

**Verification:**

- `anvil replay --to-revision 5` reconstructs the state at revision 5.
- `anvil serialize` outputs all PRDs in a consistent order.

### T009: Integrate PRD release fields into sync mapping

**Feature:** F008
**Priority:** high
**Likely files:** src/sync/mapping.py, src/models/prd.py
**Dependencies:** T003

<This task adds `prd_id` and `entity_kind` to `SyncMapping` and ensures that push stamps the owning `prd_id`. It scopes push to a specific PRD and attributes discrepancies to a PRD.>

**Acceptance criteria:**

- `SyncMapping` has `prd_id` and `entity_kind` fields.
- Push operations stamp the owning `prd_id`.
- Discrepancies are attributed to a PRD.

**Verification:**

- `anvil push --prd v0.2` pushes only to the specified PRD.
- `anvil reconcile` attributes discrepancies to the correct PRD.

### T010: Update documentation and positioning for PRD as release plan

**Feature:** F009
**Priority:** medium
**Likely files:** docs/prd.md, src/cli/help.py
**Dependencies:** T003

<This task updates skills, docs, and positioning to reframe the PRD as a release-scoped, separately-gated, revisable plan. It ensures version-lockstep, packaging manifests, and user-facing version docs are updated.>

**Acceptance criteria:**

- Documentation reflects PRD as a release-scoped plan.
- Version-lockstep and packaging manifests are updated.
- User-facing version docs are refreshed.

**Verification:**

- `anvil help` shows updated PRD documentation.
- `anvil version` displays updated version info.

### T011: Add regression tests for cross-PRD behavior

**Feature:** F004
**Priority:** high
**Likely files:** tests/regression/test_cross_prd.py
**Dependencies:** T004, T005

<This task adds regression tests to ensure that cross-PRD conflict detection, claim gates, and coordination remain intact after changes.>

**Acceptance criteria:**

- Regression tests cover cross-PRD conflict detection.
- Claim gate tests validate PRD status checking.
- Coordination tests ensure stale reaper and conflict groups work across PRDs.

**Verification:**

- `pytest tests/regression/test_cross_prd.py` passes.
- Tests assert cross-PRD behavior remains correct.

### T012: Implement replay equivalence oracle for multi-PRD DBs

**Feature:** F002
**Priority:** high
**Likely files:** src/replay/oracle.py, tests/equivalence/test_replay_equivalence.py
**Dependencies:** T001

<This task implements a replay-equivalence oracle that ensures a directly-built multi-PRD DB replays byte-identically to a pre-v7 log.>

**Acceptance criteria:**

- A replay-equivalence oracle validates byte-identical replay.
- The oracle compares pre-v7 and post-v7 states.
- The comparison is deterministic and accurate.

**Verification:**

- `anvil replay --to-end` produces identical output for both DBs.
- `anvil replay --to-revision 5` matches expected output.

### T013: Refactor legacy get_prd calls to use default PRD

**Feature:** F006
**Priority:** high
**Likely files:** src/models/prd.py, src/cli/main.py
**Dependencies:** T003

<This task audits and refactors 12 legacy `get_prd()` calls to resolve to the default PRD, ensuring correctness in a multi-PRD environment.>

**Acceptance criteria:**

- All 12 legacy `get_prd()` calls resolve to the default PRD.
- No silent fallback to default PRD occurs in incorrect contexts.
- The refactor does not break existing functionality.

**Verification:**

- `anvil status` still works as expected.
- `anvil plan` does not silently change behavior.

### T014: Implement per-PRD status rollup in anvil status

**Feature:** F006
**Priority:** medium
**Likely files:** src/cli/status.py, src/models/prd.py
**Dependencies:** T003

<This task modifies `anvil status` to roll up per-PRD stats and provide a project total.>

**Acceptance criteria:**

- `anvil status` shows per-PRD stats.
- `anvil status` provides a project-wide total.
- The rollup is deterministic and accurate.

**Verification:**

- `anvil status` shows stats for each PRD.
- `anvil status --prd v0.2` shows only that PRD's stats.

### T015: Add support for PRD-specific prune and plan operations

**Feature:** F005
**Priority:** high
**Likely files:** src/cli/plan.py, src/cli/prune.py
**Dependencies:** T006

<This task ensures that `plan --prd` prunes only that PRD's orphans and that conflict-group inference reads all PRDs' tasks.>

**Acceptance criteria:**

- `anvil plan --prd <id>` prunes only that PRD's orphans.
- Conflict group inference reads all PRDs' tasks.
- The filtering is accurate and efficient.

**Verification:**

- `anvil plan --prd v0.2` filters to only tasks in that PRD.
- `anvil plan` still detects cross-PRD conflicts.

### T016: Ensure backward compatibility for existing usage

**Feature:** F002
**Priority:** high
**Likely files:** src/cli/main.py, src/mcp/handlers.py
**Dependencies:** T001

<This task ensures that existing usage of anvil (without `--prd`) remains byte-identical to previous versions.>

**Acceptance criteria:**

- `anvil plan` behaves identically to v6.
- `anvil status` behaves identically to v6.
- All existing workflows continue to work without modification.

**Verification:**

- `anvil plan` produces identical output to v6.
- `anvil status` shows identical results to v6.

### T017: Validate migration process with real-world data

**Feature:** F002
**Priority:** high
**Likely files:** tests/integration/test_migration.py
**Dependencies:** T001

<This task validates that the v6->v7 migration works correctly with real-world data and preserves all data integrity.>

**Acceptance criteria:**

- The migration process handles large datasets correctly.
- All data is preserved without loss or corruption.
- The migration is idempotent and safe.

**Verification:**

- `anvil migrate --to 7` succeeds on a large dataset.
- `anvil status` shows all data correctly migrated.

### T018: Implement event-sourced revision tracking for PRDs

**Feature:** F007
**Priority:** high
**Likely files:** src/replay/event_source.py, src/models/prd.py
**Dependencies:** T008

<This task implements event-sourced revision tracking for PRDs, enabling non-destructive supersede and per-PRD revision counters.>

**Acceptance criteria:**

- Each PRD has a revision counter.
- Revision tracking supports non-destructive supersede.
- The system can replay to any revision.

**Verification:**

- `anvil replay --to-revision 5` works correctly.
- `anvil serialize` shows correct revision counts.

### T019: Finalize documentation and user-facing versioning

**Feature:** F009
**Priority:** medium
**Likely files:** docs/versioning.md, src/cli/help.py
**Dependencies:** T010

<This task finalizes all documentation and user-facing versioning to align with the new PRD model.>

**Acceptance criteria:**

- All documentation reflects the new PRD model.
- Versioning is consistent and clear.
- User-facing docs are updated and accurate.

**Verification:**

- `anvil help` shows updated documentation.
- `anvil version` displays correct version info.