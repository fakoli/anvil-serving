# CLI Consolidation Inventory (T006/T011/T012)

Last updated: 2026-07-10

Scope: documentation alignment and inventory for the CLI taxonomy migration (`deploy`, `external-bench`,
`cache-prune`, `score`, `voice-sidecar`, `voice start`/`voice stop`, `onboard`).

## Read-only shared-source evidence (operator-cli-v2:T002)

**Observed fact, 2026-07-10:** the shared source worktree had seven tracked, intentional dirty
paths: `README.md`, `anvil_serving/cli.py`, `anvil_serving/serves.py`,
`anvil_serving/voice/cli.py`, `docs/CLI.md`, `docs/VOICE.md`, and
`tests/voice/test_voice_cli.py`. Its `git diff --unified=0` contained **37 hunks**
(`149` additions, `78` deletions): **35 Retain, 2 Adapt, 0 Supersede**. The untracked
`unsloth_compiled_cache` was intentionally ignored. This is source-worktree evidence, not a
claim that T002 authored any of it. All user changes remain uncommitted and preserved.

No source hunk is silently dropped. Hunk ordinals below are per path in the read-only source
snapshot. `Retain` preserves the observed behavior for the named v2 follow-up; `Adapt` preserves
the behavior now while a later task changes its lifecycle contract.

| Source path | Hunk(s) and observed behavior | Disposition | Proposed v2 follow-up |
| --- | --- | --- | --- |
| `README.md` | 1: root help/version discovery; 2: voice lifecycle migration guidance. | **Retain** (2) | `T011` owns root discovery; `T012` owns voice alias lifecycle. |
| `anvil_serving/cli.py` | 1: package version import; 2: nested-command manifest; 3: global root-help options; 4: common nested-command listing; 5: canonical docs URL; 6: `-V`/`--version` dispatch. | **Retain** (6) | `T011` owns the root discovery contract. |
| `anvil_serving/serves.py` | 1: shared action descriptions; 2: action subparser help; 3: duplicate descriptions removed; 4: focused action help reuses the shared descriptions. | **Retain** (4) | `T022` owns `serves` help and action-contract follow-up. |
| `anvil_serving/voice/cli.py` | 1: `voice start` migration warning; 2: `voice stop` migration warning. | **Retain** (2) | `T012` owns the alias lifecycle. |
| `docs/CLI.md` | 1: global invocation guidance; 2: cache-prune taxonomy; 3: profile taxonomy; 4: `onboard` alias wording; 5: `serves` syntax; 6: action table; 7: `serves render` section; 8: models command index; 9: focused-section pointer; 10: cache-prune heading; 11: cache-prune syntax; 12: cache-prune behavior; 13: cache-prune example; 14: external-benchmark syntax; 15: duplicate sections removed; 16: profile section placement; 17: models-score section placement; 18: voice syntax and alias migration guidance. | **Retain** (18) | `T011`, `T012`, and `T022` own their respective canonical-reference changes. |
| `docs/VOICE.md` | 1: hide `start`; 2: hide `stop`; 3: sidecar and migration guidance. | **Retain** (3) | `T012` owns the canonical voice lifecycle. |
| `tests/voice/test_voice_cli.py` | 1: capture stderr; 2: assert `start`/`stop` migration warnings. | **Adapt** (2) | `T012` must convert this callable-alias coverage to tombstone tests before or with alias removal; `T027` owns hermetic contract coverage. |

The broader callable alias tests in `tests/test_cli.py` are also explicitly reserved for conversion:
`test_init_and_onboard_dispatch_to_same_module`, `test_onboard_alias_is_quiet`, and
`test_deprecated_root_aliases_emit_canonical_guidance`, along with
`tests/voice/test_voice_cli.py::test_start_stop_aliases_validate_and_report_ok`. `T012` must
convert them to tombstone tests before or with any alias removal. Each tombstone must prove the
legacy form exits `2`, names its canonical replacement on stderr, and never dispatches a legacy
handler. This is a proposed v2 follow-up, not a change to the observed source behavior.

## Production-polish reconciliation (operator-cli-v2:T002)

The pre-claim worktree contained ten modified paths and **49 zero-context diff hunks**. The hunk
numbers below are ordinal within each path from `git diff --unified=0` at classification time. This
is an audit of the pre-existing work, not a claim that T002 implemented it. `Retain` means keep the
working behavior for its listed owner; `Adapt` means preserve it now while the listed follow-up owns
the contract change. No pre-existing hunk is silently superseded.

| Path | Pre-existing hunks | Disposition and Operator CLI v2 owner(s) | Regression inventory |
| --- | --- | --- | --- |
| `CHANGELOG.md` | 1 | **Retain**: production discovery and alias-guidance release note; `T011`, `T012`. | Root/help tests in `tests/test_cli.py`; voice warning test in `tests/voice/test_voice_cli.py`. |
| `README.md` | 1-2 | **Retain**: root-help/version discovery and voice lifecycle compatibility notes; `T011`, `T012`. | `test_top_level_help_groups_commands_and_shows_examples`; alias tests below. |
| `anvil_serving/cli.py` | 1: version import; 2: nested-command manifest; 3-5: root-help global options, nested list, docs URL; 6: `-V`/`--version` dispatch | **Retain**: canonical root discovery contract; `T011`. | `test_top_level_help_groups_commands_and_shows_examples`, `test_top_level_version_reports_installed_version`, and the visible-command help sweep. |
| `anvil_serving/serves.py` | 1: action descriptions; 2: subparser setup; 3: duplicate local descriptions removed; 4: action parser consumes shared descriptions | **Retain**: actionable `serves --help`; `T022`. | `test_serves_help_explains_each_action` and focused action-help coverage. |
| `anvil_serving/voice/cli.py` | 1: `voice start` warning; 2: `voice stop` warning | **Retain**: hidden lifecycle compatibility guidance; `T012`. | `tests/voice/test_voice_cli.py::test_start_stop_aliases_validate_and_report_ok`. |
| `docs/CLI-CONSOLIDATION-INVENTORY.md` | 1: date; 2: scope; 3: lifecycle matrix; 4: replacement list; 5: compatibility-test reference | **Adapt**: retain the taxonomy inventory and extend it with this hunk-level audit; `T002`, `T012`. | `test_cli_consolidation_inventory_records_production_polish_audit`. |
| `docs/CLI.md` | 1: global invocation; 2-4: command-index taxonomy; 5-7: `serves` syntax/action/render section; 8-13: models/cache/profile placement; 14: external benchmark syntax; 15: duplicate command sections removed; 16: quality-loop placement; 17-18: voice syntax and migration guidance | **Retain**: canonical reference ordering and migration guidance; `T011`, `T012`, `T022`. | Live canonical-reference index test plus focused parser-help tests. |
| `docs/VOICE.md` | 1-2: hide `start`/`stop` from primary table; 3: document deprecation and `sidecar` | **Retain**: canonical voice lifecycle documentation; `T012`. | `tests/voice/test_voice_cli.py::test_start_stop_aliases_validate_and_report_ok`. |
| `tests/test_cli.py` | 1: `Path` import; 2-3: root-help expectations; 4: version test; 5: serves action-help test; 6: visible-command help sweep and CLI-reference index | **Adapt**: keep the current targeted production-polish coverage; the broad manifest/reference assertions are inputs to `T007.2` and `T027` contract suites. | This file's existing tests, plus the audit-lock test added by T002. |
| `tests/voice/test_voice_cli.py` | 1: capture stderr; 2: assert `start`/`stop` migration warnings | **Adapt**: callable compatibility coverage remains until removal is approved; `T012`, then `T027`. | Current warning assertions; tombstone conversion policy below. |

### Callable alias test conversion policy

The callable compatibility tests are deliberately retained while the aliases remain supported:
`test_init_and_onboard_dispatch_to_same_module`, `test_onboard_alias_is_quiet`, and
`test_deprecated_root_aliases_emit_canonical_guidance` in `tests/test_cli.py`, plus
`tests/voice/test_voice_cli.py::test_start_stop_aliases_validate_and_report_ok`. **T012 must
convert these to tombstone tests before or with any alias removal**, and `T027` must carry the
hermetic contract coverage. Each tombstone must prove that the legacy form exits `2`, names its
canonical replacement on stderr, and never dispatches a legacy handler. T002 does not remove a
callable alias or weaken its current migration guidance.

## Canonical verb matrix (CLI surface)

| Legacy form | Canonical form | Canonical visibility | Notes |
| --- | --- | --- | --- |
| `deploy` | `serves render` | `anvil-serving serves render` | No new root verb; command moved under `serves` family. |
| `external-bench` | `benchmark external` | `anvil-serving benchmark external` | Command remains under `benchmark` family. |
| `cache-prune` | `models cache prune` | `anvil-serving models cache prune` | `models` command now exposes cache maintenance as subcommand path. |
| `score` | `models score` | `anvil-serving models score` | `models` command now groups scoring and cache operations. |
| `voice-sidecar` | `voice sidecar` | `anvil-serving voice sidecar` | Nested under `voice` family. |
| `voice start` / `voice stop` | `voice up` / `voice down` | `anvil-serving voice up` / `anvil-serving voice down` | Hidden lifecycle compatibility forms; warn on use. |
| `onboard` | `init` | `anvil-serving init` | Alias only. |

`multiplexer` is intentionally **root-level**: it is a long-running, stateful swap service (data-plane process) and
does not function as a formatting variant of `serves`. Its operational surface would be materially narrower and
different if nested.

## Legacy alias -> canonical replacement table

- `anvil-serving deploy` -> `anvil-serving serves render`
- `anvil-serving external-bench` -> `anvil-serving benchmark external`
- `anvil-serving cache-prune` -> `anvil-serving models cache prune`
- `anvil-serving score` -> `anvil-serving models score`
- `anvil-serving voice-sidecar` -> `anvil-serving voice sidecar`
- `anvil-serving voice start` -> `anvil-serving voice up`
- `anvil-serving voice stop` -> `anvil-serving voice down`
- `anvil-serving onboard` -> `anvil-serving init` (explicit alias)

## Changed-verb coverage checklist

- [x] `serves render`
  - Syntax documented with full command form in `docs/CLI.md`.
  - Options documented: `--model`, `--gpu`, `--context`, `--served-name`, `--port`, `--out`,
    `--engine`, `--gpu-mem-util`, `--disable-thinking`, `--model-facts`, `--tier-id`, `--bind`, `--expose-lan`,
    `--manifest-out`, `--no-manifest`, and workflow flags.
  - Legacy replacement examples included with explicit mapping note.

- [x] `benchmark external`
  - Subcommands documented: `init`, `sources`, `fetch`, `import`, `list`, `report`, `export`, `compare` in
    `docs/CLI.md` and `docs/EXTERNAL-BENCHMARKS.md`.
  - `--db` and `--source`, `--url`, `--gpu`, `--model` mention paths are preserved in examples.

- [x] `models cache prune`
  - Full command form and gating flags documented in `docs/CLI.md` (`--json`, `--execute`, `--yes`, `--dry-run`,
    `--include-servable`, `--allow-empty-mixture`, `--self-check`, `--mixture`).

- [x] `models score`
  - Full command form documented in `docs/CLI.md` with supported flags (`--json`, `--no-local`, `--self-check`).

- [x] `voice sidecar`
  - Subcommands documented: `validate`, `command`, `compose`.
  - Canonical `--with-auth`, `--json`, and `--service-name` usage retained where relevant.

- [x] `onboard`
  - Alias explicitly retained in `README.md` and `docs/CLI.md`, and marked canonical alias text.

## Post-implementation reference audit

Explicit legacy command-form references should now appear only where they explain or test compatibility behavior.

| Surface | Remaining legacy references | Canonical references verified |
| --- | --- | --- |
| Product docs | `README.md`, `docs/CLI.md`, this inventory: compatibility mapping only. | `README.md`, `docs/CLI.md`, `docs/EXTERNAL-BENCHMARKS.md`, `docs/CONFIGURATION.md`, `docs/GETTING-STARTED.md`, `docs/QUALITY-GATED-ROUTER.md`, `docs/SERVES-AND-EVAL.md`, `docs/TERMINOLOGY.md`, `docs/VOICE.md`, `docs/ARCHITECTURE.md`. |
| Operator docs/skills | None as command invocations; `voice-sidecar-render` remains an artifact-kind identifier. | `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`, `skills/anvil-serving-voice-ops/SKILL.md`, `examples/openclaw/skills/anvil-serving-workbench/SKILL.md`. |
| Examples | None as command invocations; `voice-sidecar.tailnet.example` remains a sample host name. | `examples/huggingface-speech-to-speech/README.md`. |
| Tests | Compatibility coverage in `tests/test_cli.py`, `tests/test_init.py`, `tests/test_voice_sidecar.py`, and `tests/external_benchmarks/test_external_benchmarks.py`. | Canonical help/dispatch coverage in `tests/test_cli.py`, `tests/test_models.py`, `tests/test_deploy.py`, `tests/test_mcp.py`, `tests/test_voice_sidecar.py`, `tests/voice/test_voice_cli.py`, and `tests/external_benchmarks/test_external_benchmarks.py`. |
| Implementation | Hidden root aliases in `anvil_serving/cli.py`; default compatibility progs in `anvil_serving/deploy.py` and `anvil_serving/score.py`; `onboard` alias text in `anvil_serving/init.py`. | Canonical nested dispatch in `anvil_serving/serves.py`, `anvil_serving/models.py`, `anvil_serving/benchmark.py`, `anvil_serving/voice/cli.py`, `anvil_serving/voice_sidecar.py`, and MCP command guidance in `anvil_serving/mcp.py`. |

## Legacy references: keep vs rewrite

- **Keep for compatibility**
  - CLI compatibility registration and parsing behavior in `anvil_serving/cli.py` until a future runtime migration
    intentionally removes the aliases.
  - Tests that prove legacy aliases still work and either warn (`deploy`, `external-bench`, `cache-prune`, `score`,
    `voice-sidecar`, `voice start`, `voice stop`) or remain quiet (`onboard`).
  - Compatibility notes in `README.md`, `docs/CLI.md`, and this inventory.

- **Rewrite from legacy to canonical**
  - User-facing docs, examples, skills, MCP command previews, generated comments, and command help should prefer
    `serves render`, `benchmark external`, `models cache prune`, `models score`, `voice sidecar`, and `init`.
