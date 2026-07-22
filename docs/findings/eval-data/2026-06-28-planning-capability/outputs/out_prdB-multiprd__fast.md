## Tasks

### T001: Create the `prds` table schema

**Feature:** F001
**Priority:** high
**Likely files:** src/db/migrations/001_create_prds_table.py

Create a new `prds` table with columns `id` (primary key), `target_version`, `target_tag`, `is_default` (boolean), and enforce that exactly one row per project has `is_default = 1`.

**Acceptance criteria:**

- The `prds` table exists in the database schema.
- The table has a unique primary key on `id`.
- A unique constraint ensures only one row per project has `is_default = 1`.

**Verification:**

- `sqlite3 state.db "PRAGMA table_info(prds);"`
- `sqlite3 state.db "SELECT COUNT(*) FROM prds WHERE is_default = 1;"`

---

### T002: Add `prd_id` columns to requirement, feature, and task tables

**Feature:** F001
**Priority:** high
**Likely files:** src/db/migrations/002_add_prd_id_to_tables.py

Add a non-nullable `prd_id` column to the `requirements`, `features`, and `tasks` tables, with a foreign key constraint referencing `prds(id)`.

**Acceptance criteria:**

- Each of the three tables contains a `prd_id` column.
- The column is defined as NOT NULL.
- A foreign key constraint references `prds(id)`.

**Verification:**

- `sqlite3 state.db "PRAGMA table_info(requirements);"`
- `sqlite3 state.db "PRAGMA table_info(features);"`
- `sqlite3 state.db "PRAGMA table_info(tasks);"`

---

### T003: Enforce `Task.prd_id == owning Feature.prd_id` invariant

**Feature:** F001
**Priority:** high
**Likely files:** src/backend/handlers.py

Implement a check in the task creation/update logic that ensures a task’s `prd_id` matches the `prd_id` of its owning feature. Reject any operation that violates this invariant.

**Acceptance criteria:**

- Inserting or updating a task with a mismatched `prd_id` raises a validation error.
- Inserting or updating a task with a matching `prd_id` succeeds.

**Verification:**

- `python - <<'PY'\nfrom backend.handlers import create_task\ntry:\n    create_task(feature_id=1, prd_id=2, task_id=10)\nexcept Exception as e:\n    print('Error:', e)\nPY`

---

### T004: Implement v6->v7 migration to backfill a default PRD

**Feature:** F002
**Priority:** critical
**Likely files:** src/db/migrations/003_migrate_v6_to_v7.py
**Dependencies:** T001, T002

Create a migration that:

1. Adds a new PRD row marked as default.
2. Updates all existing rows in `requirements`, `features`, and `tasks` to reference this default PRD.
3. Wraps the entire operation in a single SQLite transaction.

**Acceptance criteria:**

- After migration, every row in the three tables has a non-null `prd_id` pointing to the default PRD.
- The default PRD exists and is the only row with `is_default = 1`.
- No data is lost or corrupted.

**Verification:**

- `sqlite3 state.db "SELECT COUNT(*) FROM requirements WHERE prd_id IS NULL;"`
- `sqlite3 state.db "SELECT COUNT(*) FROM features WHERE prd_id IS NULL;"`
- `sqlite3 state.db "SELECT COUNT(*) FROM tasks WHERE prd_id IS NULL;"`
- `sqlite3 state.db "SELECT COUNT(*) FROM prds WHERE is_default = 1;"`

---

### T005: Ensure migration is atomic and crash‑idempotent

**Feature:** F002
**Priority:** critical
**Likely files:** src/db/migrations/003_migrate_v6_to_v7.py
**Dependencies:** T004

Verify that running the migration twice does not corrupt the database and that the operation is wrapped in a single transaction that can be rolled back on failure.

**Acceptance criteria:**

- Running the migration a second time leaves the database unchanged.
- No partial updates remain if the migration is interrupted.

**Verification:**

- `python - <<'PY'\nimport sqlite3, os, subprocess\n# Run migration twice\nsubprocess.run(['python', 'src/db/migrations/003_migrate_v6_to_v7.py'], check=True)\nsubprocess.run(['python', 'src/db/migrations/003_migrate_v6_to_v7.py'], check=True)\nconn = sqlite3.connect('state.db')\nprint('Rows in prds:', conn.execute('SELECT COUNT(*) FROM prds').fetchone()[0])\nconn.close()\nPY`

---

### T006: Implement `get_prd(prd_id)` API

**Feature:** F003
**Priority:** high
**Likely files:** src/backend/api/prd.py

Provide a function that retrieves a PRD row by its `id`. Return `None` if not found.

**Acceptance criteria:**

- `get_prd('v0.2')` returns the correct PRD dictionary.
- `get_prd('nonexistent')` returns `None`.

**Verification:**

- `python - <<'PY'\nfrom backend.api.prd import get_prd\nprint(get_prd('v0.2'))\nprint(get_prd('nonexistent'))\nPY`

---

### T007: Implement `list_prds()` API

**Feature:** F003
**Priority:** high
**Likely files:** src/backend/api/prd.py

Return a list of all PRDs in the database.

**Acceptance criteria:**

- The returned list contains all PRDs.
- The list is sorted by `id`.

**Verification:**

- `python - <<'PY'\nfrom backend.api.prd import list_prds\nprint(list_prds())\nPY`

---

### T008: Add `prd_id` filters to list endpoints

**Feature:** F003
**Priority:** high
**Likely files:** src/backend/api/task.py, src/backend/api/feature.py, src/backend/api/requirement.py

Extend `list_tasks`, `list_features`, and `list_requirements` to accept an optional `prd_id` argument that filters results to the specified PRD.

**Acceptance criteria:**

- `list_tasks(prd_id='v0.2')` returns only tasks belonging to that PRD.
- Omitting `prd_id` returns all tasks.

**Verification:**

- `python - <<'PY'\nfrom backend.api.task import list_tasks\nprint('All tasks:', len(list_tasks()))\nprint('PRD v0.2 tasks:', len(list_tasks(prd_id='v0.2')))\nPY`

---

### T009: Update ClaimManager to respect PRD status

**Feature:** F004
**Priority:** high
**Likely files:** src/backend/claim_manager.py

Modify the claim gate so that a task can be claimed only if its owning PRD is in the `approved` state; tasks in a draft PRD are unclaimable.

**Acceptance criteria:**

- Claiming a task in an approved PRD succeeds.
- Claiming a task in a draft PRD raises a `ClaimError`.

**Verification:**

- `python - <<'PY'\nfrom backend.claim_manager import claim_task\nfrom backend.api.prd import get_prd\n# Assume task 42 belongs to PRD 'draft'\ntry:\n    claim_task(task_id=42)\nexcept Exception as e:\n    print('Error:', e)\nPY`

---

### T010: Ensure cross‑PRD conflict detection and stale reaper span

**Feature:** F004
**Priority:** high
**Likely files:** src/backend/conflict.py, src/backend/reaper.py

Run regression tests that confirm:

- Conflict groups are computed across all PRDs.
- Active‑claim exclusion and stale reaper span consider tasks from every PRD.

**Acceptance criteria:**

- No false positives or negatives in conflict detection when multiple PRDs exist.
- The stale reaper correctly cleans up tasks regardless of PRD.

**Verification:**

- `pytest tests/conflict/test_cross_prd.py`
- `pytest tests/reaper/test_stale_reaper.py`

---

### T011: Make parser `prd_id`‑load‑bearing

**Feature:** F005
**Priority:** high
**Likely files:** src/parser/parser.py

Update the parser to accept a `--prd <id>` flag, load only the specified PRD’s rows, and emit IDs in the correct format (bare for default, prefixed for named PRDs).

**Acceptance criteria:**

- Running `prd parse --prd v0.2` loads only rows with `prd_id='v0.2'`.
- IDs in the output are prefixed with `v0.2:`.

**Verification:**

- `prd parse --prd v0.2 | grep '^v0.2:'`

---

### T012: Prune orphans only for the selected PRD

**Feature:** F005
**Priority:** high
**Likely files:** src/plan/plan.py

Ensure that `plan --prd <id>` removes orphaned tasks only within that PRD, while conflict‑group inference still reads all PRDs.

**Acceptance criteria:**

- Orphans in other PRDs remain untouched after pruning a specific PRD.
- Conflict groups are still inferred across all PRDs.

**Verification:**

- `plan --prd v0.2 --dry-run | grep 'orphan'`

---

### T013: Add `--prd` flag to CLI and MCP, implement resolve_prd_id

**Feature:** F006
**Priority:** high
**Likely files:** src/cli/main.py, src/mcp/main.py, src/utils/prd_resolver.py

Introduce a `--prd` option to the CLI and MCP commands, and implement a helper that resolves the PRD ID from the flag, environment variable, or defaults to the single/default PRD. Ensure ambiguity errors are raised when multiple PRDs match.

**Acceptance criteria:**

- `anvil status --prd v0.2` shows status for that PRD.
- Ambiguous `--prd` values raise an error.

**Verification:**

- `anvil status --prd v0.2`
- `anvil status --prd ambiguous`

---

### T014: Handle ambiguity in `--prd` resolution

**Feature:** F006
**Priority:** high
**Likely files:** src/utils/prd_resolver.py

When the supplied `--prd` value matches multiple PRDs (e.g., a prefix), the resolver must raise a clear ambiguity error.

**Acceptance criteria:**

- Providing `--prd v0.` when both `v0.1` and `v0.2` exist results in an error message.

**Verification:**

- `anvil status --prd v0.`

---

### T015: Implement `prd.revised` and replay‑to‑revision logic

**Feature:** F007
**Priority:** high
**Likely files:** src/backend/revision.py

Add a `revised` column to the `prds` table to record the revision lineage. Implement non‑destructive supersede logic and a `replay_to_event_id` function that reconstructs the state as of a specific revision.

**Acceptance criteria:**

- Re‑parsing a PRD emits a new row with `revised` pointing to the previous PRD ID.
- `replay_to_event_id` returns the correct state snapshot for a given revision.

**Verification:**

- `python - <<'PY'\nfrom backend.revision import replay_to_event_id\nprint(replay_to_event_id('v0.2', revision=2))\nPY`

---