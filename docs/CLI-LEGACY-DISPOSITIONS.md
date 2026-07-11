# CLI Legacy Dispositions

Last audited: 2026-07-10

Scope: read-only reconciliation of the 12 `cli-consolidation` tasks against the
supported Anvil records, the merged PR #185 metadata, the checked-in CLI
inventory, and the current checkout. This document does not change Anvil task
state and does not claim proof that is not exposed by those sources.

## Evidence Rules

- **Command proof** means a proof recorded for the task by Anvil. The task
  records expose declared verification commands and historical
  `evidence.submitted` events, but `anvil show --json` does not expose the
  submitted proof payload or a portable result link. This audit therefore does
  not reconstruct or promote an unobserved result to proof.
- **Advisory evidence** includes PR #185 being merged, its changed-file list,
  PR description, reported CI results, and the merged
  `docs/CLI-CONSOLIDATION-INVENTORY.md`. These corroborate implementation
  coverage, but they do not replace task-level Anvil proof or human disposition.
- The current Anvil state is authoritative for disposition: T009 is `done`;
  T001-T008 and T010-T012 are `ready`.

## Observed Anvil Command References

The following read-only commands were run for this audit; each exited 0:

- **[L]** `anvil list --prd cli-consolidation` lists all 12 tasks, with T009
  `done` and T001-T008/T010-T012 `ready`.
- **[S]** `anvil status --json` reports the `cli-consolidation` rollup as 12
  total, 11 `ready`, and 1 `done`.
- **[D-T001]** through **[D-T012]** are the corresponding read-only task
  inspections, `anvil show cli-consolidation:T00N --json`. Each exposes the
  task's current state and declared `verification.commands`; those declarations
  are requirements to run and review, not proof that they have passed.
- **[D-T009]** is the required `anvil show cli-consolidation:T009 --json`
  inspection. Its `evidence.submitted` and `task.applied` history is the only
  current task-level terminal disposition in this 12-task set; the proof
  artifact identity is retained verbatim in the T009 row below.

For T001-T008 and T010-T012, the state commands prove only that Anvil still
records each task as `ready`. No task-level completion proof was observed for
those rows. The independent review/corroboration column records what PR #185,
the merged inventory, or reported review/CI can establish, and the final column
keeps the required verification and human apply gate explicit.

## Bounded Per-task Action Register

The register uses `observed proof` only for a task-level Anvil proof or a durable
proof artifact that records the command and exit code. PR review, merged commits,
CI, changed-file lists, and the checked-in inventory are independent review or
corroboration, but remain advisory and never establish acceptance.

| Task | Anvil state | Bounded disposition | Observed proof | Independent review / corroboration | Next action and gate |
| --- | --- | --- | --- | --- | --- |
| T001 | `ready` ([L], [S], [D-T001]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T001] exposes the declared T001 verification commands only. | PR #185 merge `df73c05`; root alias coverage in the merged CLI inventory is independent corroboration, not acceptance. | Run/retrieve the declared T001 verification and review it independently; human decides apply or rework. |
| T002 | `ready` ([L], [S], [D-T002]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T002] exposes the declared T002 verification commands only. | PR #185 merge `df73c05`; production-polish and voice taxonomy entries in the inventory are corroboration, not acceptance. | Run/retrieve the declared T002 verification and review it independently; human decides apply or rework. |
| T003 | `ready` ([L], [S], [D-T003]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T003] exposes the declared T003 verification commands only. | PR #185 merge `df73c05`; `models cache prune` inventory coverage is corroboration, not acceptance. | Run/retrieve the declared T003 verification and review it independently; human decides apply or rework. |
| T004 | `ready` ([L], [S], [D-T004]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T004] exposes the declared T004 verification commands only. | PR #185 merge `df73c05`; `models score` inventory coverage is corroboration, not acceptance. | Run/retrieve the declared T004 verification and review it independently; human decides apply or rework. |
| T005 | `ready` ([L], [S], [D-T005]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T005] exposes the declared T005 verification commands only. | PR #185 merge `df73c05`; help normalization is advisory corroboration, not acceptance. | Run/retrieve the declared T005 verification and review it independently; human decides apply or rework. |
| T006 | `ready` ([L], [S], [D-T006]) | Documentation candidate; no completion proof observed | No task-level completion proof observed. [D-T006] exposes the declared T006 review/search commands only. | PR #185 merge `df73c05`; inventory records canonical and compatibility references, but named skill files still require independent review. | Run/retrieve the declared T006 checks and review the named skill files; human decides apply or rework. |
| T007 | `ready` ([L], [S], [D-T007]) | Implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T007] exposes the declared T007 verification commands only. | PR #185 merge `df73c05`; MCP/controller guidance and tests are advisory corroboration; safety preservation requires independent review. | Run/retrieve the declared T007 verification and review safety preservation; human decides apply or rework. |
| T008 | `ready` ([L], [S], [D-T008]) | Verification reported; no Anvil completion proof observed | No task-level completion proof observed. [D-T008] exposes the declared full-suite/help verification commands only. | PR #185 reports focused/full tests, CI, and adversarial review; these are independent review/corroboration, not Anvil proof or acceptance. | Re-run/retrieve T008 verification and review findings independently; human decides apply or rework. |
| T009 | `done` ([L], [S], [D-T009]) | Implemented, three required proofs observed, and applied | Anvil proof artifact `cli-consolidation-T009-E019467.json` records final exit 0 for `python -m pytest tests/test_deploy.py tests/test_cli.py -q`, `python -m anvil_serving.cli serves render --help`, and `python -m anvil_serving.cli deploy --help`. It also records an earlier pytest exit 1; the later passing run is the observed passing proof. | Implementation commit `86d11cb` was merged by PR #185 as `df73c05`; `anvil show` exposes `evidence.submitted` and `task.applied` events | No new action; retain `done`. PR/CI do not substitute for the three proofs. |
| T010 | `ready` ([L], [S], [D-T010]) | Corrected canonical path; implemented candidate; no completion proof observed | No task-level completion proof observed. [D-T010] exposes the declared verification commands; the corrected test path is `tests/external_benchmarks/test_external_benchmarks.py`. | PR #185 merge `df73c05`; canonical path is `benchmark external`, with `external-bench` as hidden compatibility; corroboration is not acceptance. | Run/retrieve the declared T010 verification using `tests/external_benchmarks/test_external_benchmarks.py`, then human decides apply or rework. |
| T011 | `ready` ([L], [S], [D-T011]) | Root-boundary candidate; no completion proof observed | No task-level completion proof observed. [D-T011] exposes the declared root/help boundary checks only. | PR #185 merge `df73c05`; inventory reports the root-boundary audit, which still requires independent review. | Run/retrieve the declared T011 checks and review root/help boundaries; human decides apply or rework. |
| T012 | `ready` ([L], [S], [D-T012]) | Migration inventory candidate; no completion proof observed | No task-level completion proof observed. [D-T012] exposes the declared migration-inventory checks only. | PR #185 merge `df73c05`; inventory and verb matrix are advisory corroboration, not acceptance. | Run/retrieve the declared T012 checks and review migration counts/categories; human decides apply or rework. |

## Historical `cli-usability` Limitation

The historical `cli-usability` records list six tasks as `done`, including
T006, and expose `evidence.submitted`/`task.applied` events. Their JSON output
still does not expose portable proof payloads or links. They are therefore
historical state and advisory context for this reconciliation, not independent
proof of the current `cli-consolidation` tasks. In particular, a historical
done status must not be copied into the current matrix as proof for a different
task.

## Human Apply Decisions Pending

The following eleven decisions remain separate because Anvil marks these tasks
`ready`: `cli-consolidation:T001`, `T002`, `T003`, `T004`, `T005`, `T006`,
`T007`, `T008`, `T010`, `T011`, and `T012`. For each one, the reviewer must
compare acceptance criteria and verification commands with independently
retrievable task-level proof, choose accept or rework through the normal Anvil
review gate, and avoid treating advisory PR/CI/inventory evidence as acceptance.
T009 is the only terminal disposition observed in the requested 12-task listing.
This audit intentionally does not submit evidence, apply tasks, release claims,
or otherwise mutate Anvil state.
