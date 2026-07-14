# ADR-0020 — `init` defaults to the home scaffold, shipped as package data

- **Status:** **Accepted** (2026-07-13)
- **Date:** 2026-07-13
- **Relates to:** [ADR-0003](0003-portable-defaults-and-generic-onboarding.md) (generic onboarding —
  this ADR flips its default surface and fixes the packaging of the scaffold set) ·
  [ADR-0019](0019-anvil-serving-owns-the-tailnet-edge.md) (the `edge.toml` the scaffold emits) ·
  `anvil_serving/init.py`, `anvil_serving/_scaffold_templates/`, `pyproject.toml`,
  `scripts/sync_scaffold_templates.py`

## Context

PR #252 added `init --home`: a full operational-config scaffold (all `serves*.toml`, compose files,
`operator-topology.toml`, the voice manifest, `.env.example`, and the ADR-0019 `edge.toml`) written
into the config home so a fresh machine runs `serves up --group NAME` with zero hand-assembly. It
shipped with two defects:

1. **Packaging — the installed tool is broken.** `init --home` resolved its reference files via
   `__file__/../examples` (`_EXAMPLES_DIR`). That path only exists in a **source checkout**; the
   `examples/` tree is not inside the `anvil_serving` package and is **not shipped** in the wheel
   (`pyproject.toml` `package-data` never included it). Running the console-script from a normal
   `pip`/`uv tool install` therefore failed with *"the shipped reference examples are not available
   next to this install."* The feature worked only for developers running from a git checkout.

2. **Wrong default (operator directive).** Bare `init` still did the single-model, single-file
   bring-up into the CWD, and the full operational scaffold — the thing an operator standing up a
   machine actually wants — was hidden behind `--home`. The operator asked for the reverse.

The build backend is **setuptools** (`pyproject.toml` `[build-system]`). Setuptools can only ship
data resolvable via `importlib.resources` when it lives **inside** a package directory; files under
the repo-root `examples/` tree cannot be included as importable package resources without moving or
mirroring them.

## Considered options

1. **`data_files` / `MANIFEST.in` the `examples/` tree.** `MANIFEST.in` only affects the sdist, not
   the wheel; `data_files` installs outside the package and is not addressable via
   `importlib.resources.files(anvil_serving)`. Rejected: neither makes the set resolvable from an
   installed console-script the way the code needs.
2. **Move `examples/fakoli-dark` into the package.** Rejected: `examples/` is a real, browsable
   reference instance with far more than the scaffold subset; relocating it churns docs and the CLI
   audit inventory for no benefit.
3. **Mirror the needed subset into a package data dir, resolve via `importlib.resources`, and guard
   the mirror against drift with a test + sync script.** Chosen. The canonical copies stay in
   `examples/`; the wheel ships a byte-identical mirror it can actually find.

## Decision

- **Ship the scaffold set as package data.** The subset `init` needs is mirrored into
  `anvil_serving/_scaffold_templates/` and declared in `pyproject.toml`
  (`[tool.setuptools.package-data]` → `"anvil_serving" = ["_scaffold_templates/*"]`). `init` resolves
  every template via `importlib.resources.files("anvil_serving._scaffold_templates")` — never
  `__file__/../examples`. This works identically from a wheel install and a source checkout.
- **No drift.** `examples/fakoli-dark/` + `examples/voice/` remain the canonical copies.
  `scripts/sync_scaffold_templates.py` regenerates the mirror; `tests/test_init.py`
  (`test_scaffold_templates_match_examples`) fails the suite if the mirror ever diverges, and a
  packaged-path test resolves the set the way an installed tool does so the #252 regression cannot
  return.
- **Flip the default.** Bare `anvil-serving init` now scaffolds the full operational config home
  (default target `~/.anvil-serving`, honoring `ANVIL_SERVING_HOME`; override with `--out-dir`). The
  single-model CWD bring-up moves behind **`--single-model`**. `--home` remains as a **hidden,
  deprecated alias** for one release (prints a deprecation note), then is removed.
- **Safety preserved.** No-overwrite-without-numbered-backup and placeholder-only (no secrets, no
  real GPU UUIDs or tailnet IP) behavior are unchanged for both paths.

## Consequences

- **Breaking change (pre-1.0, operator-requested).** Anyone scripting `anvil-serving init` for the
  old single-file CWD bring-up must add `--single-model`. Recorded in `CHANGELOG.md` under Unreleased.
- **The installed tool works.** `uv tool install` / `pip install` of anvil-serving can run `init`
  from any cwd and get the full set with no *"examples not available"* error.
- **New maintenance obligation:** when a mirrored source file under `examples/` changes, run
  `scripts/sync_scaffold_templates.py` (the drift test will otherwise red the suite). This is the
  deliberate cost of setuptools not shipping out-of-package files as importable resources.
- ADR-0003's substance (portable defaults, a generated single-model bring-up) is unchanged — only its
  default command surface moves behind `--single-model`.
