# Down-container cleanup

## Problem

Model experiments were stopped but left in Docker after a candidate swap. The
stale containers retained old model commands and Compose configuration, making
the live serving inventory look more complicated than it was.

## Decision

- `anvil-serving serves down` stops and removes selected containers by default.
- `--keep-container` retains the stopped container and its logs when deliberate.
- The `serves_manage` controller exposes the same choice as
  `keep_container=true`.
- Managed voice audio/proxy teardown inherits the cleanup default; native
  process teardown remains process-only.
- Promotion transactions retain displaced containers temporarily so rollback
  evidence and logs remain available during the transaction.

## Live cleanup

Removed the stopped `vllm-nemotron3-omni` experiment through the guarded
`serves_manage` controller. Stopped Workbench, Postgres, and Neo4j containers
were left untouched because they belong to the separate persistent Workbench
stack.

## Verification

- Focused lifecycle/controller/voice gate: 419 passed.
- Full repository suite: 3,135 passed, 2 skipped.
- Ruff: passed.
- Full CLI reference audit: 481 files, zero violations; inventory and generated
  artifacts current.
- Strict MkDocs build and 194-file Markdown link check: passed.
