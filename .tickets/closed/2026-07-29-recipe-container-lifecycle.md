# Recipe-loaded candidate lifecycle must stay inside Anvil Serving

## Problem

`models recipes load` could start an isolated candidate, but the product had no
matching recipe-aware status, bounded-log, or teardown commands. `serves logs`
only resolved manifest entries, and the load failure text instructed operators
to use raw `docker logs`. During the Agents-A1/Qwen head-to-head this forced a
narrow Docker fallback for logs and encouraged raw Docker teardown even though
recipes are the intended reproducibility and sharing boundary.

## Resolution

- Recipe load now labels every candidate with
  `io.anvil-serving.managed-by=models-recipes`, the exact recipe model, and the
  pinned download revision when present.
- Added `models recipes status MODEL --container NAME`.
- Added bounded `models recipes logs MODEL --container NAME`.
- Added guarded `models recipes unload MODEL --container NAME`, including
  dry-run, confirmation, exact ownership verification, and a second identity
  check immediately before removal.
- Unlabeled containers and model/revision mismatches fail closed.
- Load preview and readiness-failure guidance now use only the Anvil recipe
  lifecycle commands.
- Added the operator workflow to `docs/cli/models.md` and the repository rule
  to `AGENTS.md`.

## Verification

- Focused recipe, model CLI, and multimodal tests: 157 passed.
- Full repository suite: 3,356 passed and 8 skipped.
- Ruff, strict MkDocs, tracked Markdown link validation, and the full
  553-file CLI inventory check passed.
