# The live operator CLI is an editable install pointing at a scratch worktree

**Status:** Open — operational hazard on Fakoli Dark; no product code defect

## Problem

`anvil-serving` on Dark is not an installed release. `pip show anvil-serving`
reports:

```text
Version: 0.23.0
Location: .../site-packages
Editable project location: <product-checkout>/.claude/worktrees/docs-portal-cleanup-0e27d3
```

The command that performs every durable lifecycle operation — `serves mode
enter`, `serves up/down`, `router` management, promotion — executes code from a
**temporary Claude Code worktree**, on whatever branch that worktree currently
has checked out. `.claude/worktrees/` is scratch space: created per task,
switched between branches freely, and removed when the task ends.

Three consequences, all observed on 2026-08-08:

1. **Branch switches silently change the control plane.** During this session
   the worktree moved from `claude/ponytail-audit-cuts` to a new fix branch.
   The live `anvil-serving` command changed underneath the running system at
   that moment, with no signal to the operator.
2. **The reported version was wrong.** Source declared `0.23.1` while the
   installed distribution metadata still said `0.23.0`, because editable
   metadata is only regenerated on reinstall. `anvil-serving --version`
   therefore reported a version that did not match the code being executed.
   `tests/test_cli.py::test_top_level_version_reports_installed_version` is
   the existing guard and it fails locally whenever this drift is present.
3. **Worktree removal would uninstall the control plane.** Deleting the
   worktree — the normal end of a Claude Code task — leaves an editable
   install whose target no longer exists. Recovery requires reinstalling
   before any serve can be managed again.

This is the same failure shape as
`2026-08-08-operator-registry-paths-depend-on-git-worktrees.md`, one layer up:
there a promoted recipe depended on a disposable checkout, here the tool that
reads it does.

## Evidence

- The live promoted DeepSeek primary was brought up in this session using this
  editable install; the module resolved to
  `<product-checkout>/.claude/worktrees/docs-portal-cleanup-0e27d3/anvil_serving/__init__.py`.
- `importlib.metadata.version("anvil-serving")` returned `0.23.0` against a
  source tree declaring `0.23.1`, until `pip install -e . --no-deps` was rerun.
- No product code is implicated. `cli._installed_version()` correctly prefers
  distribution metadata and falls back to `__version__`; the metadata it read
  was simply stale.

## Required behavior / operator action

1. Operator: install the operator CLI from the canonical checkout or from a
   built wheel, not from `.claude/worktrees/*`. A durable control plane should
   not live in scratch space. If an editable install is wanted for development,
   anchor it on the canonical clone and keep feature work in worktrees that are
   exercised through `python -m anvil_serving.cli` from inside that worktree.
2. Operator: after any deliberate version change to an editable install, rerun
   the install so distribution metadata and `__version__` agree. Treat a
   failing `test_top_level_version_reports_installed_version` as this drift
   rather than as a source defect.
3. Product (optional hardening): `anvil-serving doctor` could report when the
   running package resolves inside a linked git worktree — the same
   `git rev-parse --git-common-dir` vs `--git-dir` test proposed in the
   registry-path ticket — since an operator has no other routine signal that
   their control plane is transient.
