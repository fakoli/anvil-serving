# Git hooks

Installed via `core.hooksPath` (set in the clone's local config; worktrees
share it):

    git config core.hooksPath .githooks

## Hooks

- `pre-commit` — regenerates the generated CLI-reference artifacts
  (`docs/CLI.md` index block, `docs/CLI-REFERENCE-AUDIT.json`,
  `tests/fixtures/cli_reference_audit/expected.json`) and stages them, so
  every commit is self-consistent. Blocks the commit when the audit reports
  active legacy-reference violations (the same condition that gates CI).
- `pre-push` — hermetic backstop: validates the pushed SHA in a throwaway
  worktree. If the pushed tree would fail the docs-audit check (stale
  inventory), it regenerates the artifacts, commits them on top of the
  pushed SHA, fast-forwards the local branch, and rejects the push — the
  next `git push` delivers the fresh state.

pre-commit is a no-op in checkouts without `scripts/audit_cli_references.py`;
pre-push is fail-closed there (it cannot verify, so it rejects the push with
a message).
