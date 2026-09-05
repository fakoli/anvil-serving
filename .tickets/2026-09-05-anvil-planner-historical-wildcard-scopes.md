# Historical task scopes block a new Anvil plan

- Status: mitigated; upstream planner correction remains open
- Scope: delivery coordination, not Anvil Serving runtime
- Observed tool: Anvil 0.6.5 development build, schema 21

## Reproduction

In a State project containing completed tasks whose `likely_files` include
wildcards, plan a new partition with only valid portable relative file paths.
`anvil plan --prd <new-partition> --no-llm --json` fails atomically with
`path_identity_error`. Conflict inference includes historical completed tasks
and rejects their old wildcard scopes before creating the new task graph.

The observed legacy patterns included `anvil_serving/harness/*`, `tests/*`,
and `.codex/agents/anvil-*.toml`. No task history or database was modified.

## Bounded mitigation

Initialize an isolated delivery State in the dedicated product worktree using
the supported `ANVIL_ROOT` override, import the four reviewed public PRD sources,
and plan there. Use that root consistently for claims and evidence. Keep its
ignored runtime data out of source publication and retain the worktree until
State is safely migrated. This succeeded for both router partitions.

## Upstream completion criteria

The Anvil planner must either exclude terminal tasks from actionable conflict
inference or offer a supported explicit migration of historical scope metadata.
Add a regression with an old completed wildcard task and a new portable task;
preserve active-task conflict validation and immutable historical evidence.
The isolated-project mitigation does not prove that upstream correction.
