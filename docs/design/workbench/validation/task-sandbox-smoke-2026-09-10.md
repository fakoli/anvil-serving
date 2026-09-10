# Task evidence sandbox smoke — 2026-09-10

This receipt records a disposable local smoke of the Workbench task-evidence
sandbox. It did not read or mutate Anvil State, a production project checkout,
or a live Pi session.

## Environment

- Runner image observed from the configured private Pi policy:
  `sha256:fb67c2e17ab613e028334a6384b412db2658fb36cbb16e9a6312f587bb21fd26`.
- Docker image inspection confirmed that digest was available locally. A
  separately supplied digest was not available, so it was not used.
- The sandbox used the configured Docker executable, UID/GID, a network-none
  container, read-only root filesystem, dropped capabilities, no-new-privileges,
  CPU/memory/PID limits, and a forced `/bin/bash` entrypoint.

## Scenario and observed results

A temporary, local Git repository was initialized solely for this probe. The
Anvil-style claim tree stayed at its baseline. The server created two private
full clones before the runner tree was changed. The runner clone then received:

- one committed change to `src/base.txt`;
- one untracked `src/untracked.txt`; and
- a hostile local `core.fsmonitor` setting that exits unsuccessfully if used.

Sandbox capture returned the original baseline and a 314-byte patch with the
two expected paths. No host Git command inspected the changed runner checkout.

The frozen command `false` produced exit code 1. The retained verifier result
reported `applied: false`; the claim tree remained baseline-clean. The frozen
passing command checked both the untracked file and committed content. It
returned exit code 0 and transferred the same reviewed patch. A post-transfer
read of the disposable claim tree reported the modified tracked file and the
untracked file.

The smoke used bounded tool observations; raw container output was not retained.
The temporary repository was deleted after the checks.

The final follow-up smoke repeated the failed and passing checks after splitting
verification and transfer into separate containers. The verification container
had no `/claim` mount; its frozen commands could only alter `/verify`. The
fixed transfer container ran only after a passing result and a container-side
baseline comparison. The temporary claim tree again showed the expected tracked
and untracked changes after transfer. The linked-worktree `.git` masking branch
is covered by focused contract tests; this disposable smoke used a normal
in-tree `.git` repository.

## Focused automated validation

```
python scripts/run_tests.py tests/workbench/test_projects.py tests/workbench/test_task_artifacts.py tests/workbench/test_task_sandbox.py -q
python -m ruff check anvil_serving/workbench_app/projects.py anvil_serving/workbench_app/task_artifacts.py anvil_serving/workbench_app/task_sandbox.py tests/workbench/test_projects.py tests/workbench/test_task_artifacts.py tests/workbench/test_task_sandbox.py
```

At the time of this receipt, the focused suite reported 17 passing tests and
Ruff reported no findings. The browser/service asynchronous wiring is validated
separately; this receipt covers the sandbox and evidence boundary only.
