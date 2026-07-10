# CLI Consolidation Inventory (T006/T011/T012)

Last updated: 2026-07-10

Scope: documentation alignment and inventory for the CLI taxonomy migration (`deploy`, `external-bench`,
`cache-prune`, `score`, `voice-sidecar`, `onboard`).

## Canonical verb matrix (CLI surface)

| Legacy form | Canonical form | Canonical visibility | Notes |
| --- | --- | --- | --- |
| `deploy` | `serves render` | `anvil-serving serves render` | No new root verb; command moved under `serves` family. |
| `external-bench` | `benchmark external` | `anvil-serving benchmark external` | Command remains under `benchmark` family. |
| `cache-prune` | `models cache prune` | `anvil-serving models cache prune` | `models` command now exposes cache maintenance as subcommand path. |
| `score` | `models score` | `anvil-serving models score` | `models` command now groups scoring and cache operations. |
| `voice-sidecar` | `voice sidecar` | `anvil-serving voice sidecar` | Nested under `voice` family. |
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
    `voice-sidecar`) or remain quiet (`onboard`).
  - Compatibility notes in `README.md`, `docs/CLI.md`, and this inventory.

- **Rewrite from legacy to canonical**
  - User-facing docs, examples, skills, MCP command previews, generated comments, and command help should prefer
    `serves render`, `benchmark external`, `models cache prune`, `models score`, `voice sidecar`, and `init`.
