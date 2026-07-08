# Anvil score PRD-scope gap (2026-07-07)

> **STATUS: CONFIRMED in Anvil 0.4.2.** The `score --prd <id>` command scopes
> the scoring pass to the selected PRD, but the post-score expansion queue is
> built from all tasks in the project. On multi-PRD workspaces this can surface
> completed or unrelated tasks from other PRDs as if they are actionable in the
> current scoring run.

## Environment

- Product: Anvil State
- Runtime checked: `anvil 0.4.2 (schema 8)`
- Plugin cache: `C:\Users\sdoum\.codex\plugins\cache\anvil\anvil\0.4.2`
- Installed revision: `2fbb37196a6a2879d6ae9cc91521e62d61a14fce`
- Project workspace: `anvil-serving`
- State path: `C:\Users\sdoum\.anvil\workspaces\anvil-serving-7a68b006\.anvil`
- Affected PRD used for reproduction: `voice-latency-model-ab`

## Symptom

After planning and scoring the named PRD, the visible scoring table correctly
listed only `voice-latency-model-ab` tasks. The `EXPANSION QUEUE` printed after
the same run included high-complexity tasks from unrelated, completed PRDs such
as `advise-and-defer`, `genericity`, `openclaw-anvil-voice-option`,
`operator-skills-subagents`, and `voice-pipeline`.

The queue looked actionable, but most entries were historical `done` tasks.
That makes the planning loop noisy and can steer an agent into expanding or
discussing the wrong work.

## Reproduction

The current PRD has one high-complexity task:

```powershell
python -m anvil.cli list --prd voice-latency-model-ab
```

Result summary:

| Scope | Expansion-threshold candidates |
|---|---:|
| All PRDs | 14 |
| `voice-latency-model-ab` only | 1 |

Scoped candidate:

| PRD | Task | Status | Complexity | Title |
|---|---|---|---:|---|
| `voice-latency-model-ab` | `voice-latency-model-ab:T003` | `drafted` | 4 | Add or tighten CLI support for candidate voice benchmarks |

Sample unrelated candidates that leaked into the all-PRD queue:

| PRD | Task | Status | Complexity |
|---|---|---|---:|
| `advise-and-defer` | `advise-and-defer:T010` | `done` | 4 |
| `genericity` | `genericity:T006` | `done` | 4 |
| `openclaw-anvil-voice-option` | `openclaw-anvil-voice-option:T004` | `done` | 4 |
| `operator-skills-subagents` | `operator-skills-subagents:T001` | `done` | 4 |
| `voice-pipeline` | `voice-pipeline:T001` | `done` | 4 |

## Root Cause

The scoring command resolves and applies the PRD scope when selecting tasks to
score:

```python
scoped_prd_id = canonical_prd_id(resolve_prd_id(backend, prd)) if prd else None
all_tasks = backend.list_tasks(prd_id=scoped_prd_id)
tasks_to_score = [t for t in all_tasks if not _scores_complete(t)]
```

After scoring, the expansion queue re-fetches without that same filter:

```python
expansion_queue = (
    build_recursive_expansion_queue(
        backend.list_tasks(), threshold=expand_threshold
    )
    if auto_expand
    else []
)
```

In Anvil 0.4.2 this is in:

`bin/src/anvil/cli/plan.py`, around the score command's post-score queue build.

## Impact

- Multi-PRD workspaces show unrelated expansion work after a scoped scoring run.
- Completed `done` tasks can appear in an expansion queue, even though they are
  not actionable planning work.
- Agent workflows that follow the plan skill literally may pause for irrelevant
  expansion decisions or attempt to expand historical tasks.
- The JSON `score --prd ... --json` output is only clean when no tasks require
  scoring, because the command returns early with an empty queue before it hits
  the all-PRD queue construction path.

The issue does not corrupt task scores. The bad behavior is queue presentation
and action selection after scoring.

## Recommended Fix

The expansion queue should use the same task collection as the scoring run when
a PRD scope is active:

```python
queue_tasks = backend.list_tasks(prd_id=scoped_prd_id)
```

It should also filter out terminal or non-actionable statuses before presenting
work for expansion. A conservative status allowlist would be:

```text
proposed, drafted, reviewed, ready
```

That keeps already completed, rejected, or review-held work out of the expansion
queue.

If a single-task score is requested, the queue should either:

- be limited to that task's owning PRD, or
- be omitted unless an explicit all-PRD queue flag is provided.

## Test Coverage To Add Upstream

Add a multi-PRD fixture with:

- PRD A containing a `done` task with `complexity=4`.
- PRD B containing a `drafted` task with `complexity=4`.
- A scoped command: `score --prd <PRD B>`.

Assertions:

- The scoring table includes only PRD B tasks.
- The expansion queue includes only PRD B candidates.
- `done` tasks are absent from the queue.
- The JSON envelope's `expansion_queue` matches the human-readable queue.

## Operational Workaround

Until fixed upstream:

- Treat `score --prd ...` scoring values as valid.
- Ignore `EXPANSION QUEUE` entries whose task id does not start with the active
  PRD id.
- Ignore `done` tasks in the expansion queue.
- Use `anvil list --prd <id> --json` or a direct state inspection to identify
  scoped high-complexity tasks before expanding anything.
