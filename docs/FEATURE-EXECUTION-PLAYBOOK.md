# Feature execution playbook

**Audience:** an implementing agent (or contributor) executing a feature issue
cold, without this repository's session history. Every open feature issue
references this document. It captures the wiring recipes, verification gates,
and environment gotchas learned while shipping features 1–8 and 11 of
`STRATEGY-MAKE-DIVERGENCE-LOUD.md` (PRs #363–#372).

## The gate sequence (every feature, no exceptions)

1. Implementation + tests on a branch cut from `origin/main`.
2. `python -m pytest tests/ -q` fully green (see *known local failures* below).
3. **Mutation check**: break your own logic (invert a comparison, disable a
   check) and confirm at least one test fails; restore. A test suite that
   passes against a broken implementation is not done. Verify the mutation hit
   *code*, not a comment or docstring — grep the emission site first (this
   mistake happened twice in one day).
4. `python -m ruff check anvil_serving tests` clean.
5. `python -m mkdocs build --strict` clean; user docs updated.
6. CLI reference audit: `git add -A` **first** (the audit is `git ls-files`
   based — an unstaged new file is invisible and the inventory reads stale),
   then `python scripts/audit_cli_references.py --update --scope docs|skills|full`.
7. CHANGELOG entry + version bump in **both** `pyproject.toml` and
   `anvil_serving/__init__.py`.
8. PR with: what it closes (name the incident), what the gates caught,
   verification tails. Squash-merge only after all CI legs pass.
9. Live validation on the reference fleet is an **operator step** — mark
   anything requiring SSH or a running serve as `[operator]` in the issue and
   do not attempt it from CI or tests.

## Wiring recipe A: a new `serves` action

Mirror `serves lint` / `serves rollback-check` (see `anvil_serving/serves.py`).
Five edits, all required:

1. Pure report function + `cmd_*` printer in `serves.py`, near
   `lint_manifest_set`. Report dict shape: `{"findings": [{check, severity,
   serve, detail, files}], "errors": n, "warnings": n, ...}` — tooling parses
   this; match it exactly.
2. `_ACTIONS` tuple + `_ACTION_DESCRIPTIONS` dict.
3. Parser branch: names/`--json`/`use_set` membership sets (grep for
   `"rollback-check"` to find all of them). Strict loader unless the command's
   *purpose* is reporting defects the strict loader refuses (only `lint` loads
   leniently — that asymmetry is deliberate; see PR #365).
4. Dispatch branch in the main command function.
5. `_resource_node` in `anvil_serving/commands/serves.py`, then regenerate:
   `python -c "from anvil_serving.commands import spec; spec.write_manifest()"`
   (the checked-in manifest is byte-compared by `tests/test_command_tree.py`).

**Confirmation gating:** a node with `mutation="mutate"` plus a plain
`--confirm` option demands confirmation for EVERY invocation, including
read-only ones. If the command has a read path, use the conditional-gate
convention instead: `requires_confirmation=True` on the `--confirm` option
itself (the `switch --recipe` pattern; see PR #369, which shipped this bug and
its fix). Pin the read path with a CLI-level test that calls `cli.main(...)` —
leaf-function tests cannot see dispatcher gating.

## Wiring recipe B: a new `fleet` verb

Mirror `fleet version` / `fleet drift` (`anvil_serving/fleet.py`,
`anvil_serving/commands/fleet.py`). Subparser in `fleet.py::_build_parser` +
dispatch in `main()`; `_resource_node` sibling in `commands/fleet.py`
(`role="operator"` — the existing role for commands not owned by one topology
resource); regenerate the manifest. The family is already registered in
`commands/registry.py`; adding a verb does not touch it.

Reuse, don't re-derive: `_local_hostname_matches` (local/remote
classification), the ssh probe pattern, `repo_tracked_files`/`_repo_hashes`.

## Environment gotchas (every one of these caused a real failure)

- **Remote Windows hosts**: no `python3` (a Store stub answers "Python was not
  found" and exits nonzero) — fall back to `python` on launcher misses only.
  The remote shell is cmd.exe: ssh joins argv with spaces and the remote
  re-splits, so any `-c` payload containing a space must be client-side
  quoted; multi-line payloads must be base64-wrapped into one shell-inert
  argument. `ssh -n` blocks stdin — heredocs cannot reach the remote.
- **ssh stderr is polluted** by client-side advisory banners (`** WARNING:
  ... post-quantum ...`). Filter `** `-prefixed lines before reporting stderr
  as a failure detail, or the banner masquerades as the error.
- **Hostname vs host id**: repos key hosts by short name (`dark`); machines
  report `fakoli-dark` or `fakoli-mini.local`. Match on the first DNS label,
  then `-`-token membership. Bare containment is wrong (`"w" in
  "elsewhere"`); bare prefix is wrong (`dark` vs `fakoli-dark`).
- **CRLF**: Windows checkouts flip line endings; content comparisons must
  normalize `\r\n` → `\n` or every file "differs".
- **Compose profiles**: `docker compose config` EXCLUDES `profiles:`-gated
  services unless `--profile` is passed. Every real rollback serve is
  profile-gated — dropping profiles silently skips exactly what a rollback
  check exists to verify (PR #368's blocker). Parse the whole invocation from
  the serve's `up` argv: every `-f`/`--file`(`=`), every `--profile`, the
  service names after `up`.
- **`--json` at the top-level CLI** is wrapped in the standard result envelope
  (like `groups`); the leaf module (`python -m anvil_serving.<module>`) emits
  the raw report. Tests should call the leaf or `cmd_*` directly.
- **Known local failures**: `test_cli.py::test_top_level_version_reports_
  installed_version` fails locally whenever the source version is bumped but
  the editable install's metadata is stale. It is environment drift, not your
  change (see `.tickets/2026-08-08-live-cli-is-an-editable-install-on-a-
  scratch-worktree.md`). CI installs fresh. Never "fix" it by reinstalling
  from a scratch worktree.

## Test conventions

- Inject `_run=subprocess.run` and fake it; **no real docker, ssh, or network
  in tests**. Copy the `_Result` / fake-runner helpers from
  `tests/test_serves_rollback_check.py` or `tests/test_fleet_drift.py`.
- Serve-entry fixtures need `runtime = "docker"` (required since 0.24.0).
- One sharp test per behavior; no permutation matrices. Every error message a
  user can hit should appear in at least one assertion.
- If the spec you are executing contradicts itself, resolve it in favor of the
  narrower guarantee, say so in your PR, and do not silently pick.

## Design rules that bound every feature

- **Detection beats prevention beats automation.** Report first; refuse only
  states with *no legitimate form* (duplicate serve names: yes; a
  not-yet-created registry file: no — see the feature-5 revision in the
  strategy doc); never auto-heal (ADR-0033).
- Silent success on a no-op input is a defect: a typo'd `--restore-group`
  matching nothing must error, not pass (PR #368).
- Never read or transmit `.env` or credential files — prefer designs where
  they are structurally unreachable (only repo-tracked names ever touched),
  not filtered out.
- `127.0.0.1` is host-relative; never substitute `localhost`. Translations
  (e.g. `host.docker.internal` → `127.0.0.1` when probing from the host) must
  be reported in output, never silent.
- Unreachable is an availability state, not an error (ADR-0034 §6): sleeping
  laptops exit 0; skew and not-installed exit 1.
- Every feature must be usable on a fleet of one.
