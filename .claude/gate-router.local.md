---
rules:
  - "anvil_serving/**" => python -m ruff check .
  - "scripts/**" => python -m ruff check .
  - "tests/**" => python -m ruff check .
  - "pyproject.toml" => python -m ruff check .
  - "anvil_serving/**" => python -m pytest tests/ -q
  - "tests/**" => python -m pytest tests/ -q
  - "pyproject.toml" => python -m pytest tests/ -q
  - "anvil_serving/cli.py" => python scripts/audit_cli_ux.py --check
  - "anvil_serving/command_tree.py" => python scripts/audit_cli_ux.py --check
  - "docs/CLI-COMMAND-MANIFEST.json" => python scripts/audit_cli_ux.py --check
  - "docs/CLI-UX-AUDIT.json" => python scripts/audit_cli_ux.py --check
  - "anvil_serving/cli.py" => python scripts/audit_cli_references.py --check --scope full
  - "anvil_serving/command_tree.py" => python scripts/audit_cli_references.py --check --scope full
  - ".agents/skills/**" => python scripts/audit_cli_references.py --check --scope full
  - ".claude/skills/**" => python scripts/audit_cli_references.py --check --scope full
  - "skills/**" => python scripts/audit_cli_references.py --check --scope full
  - "examples/openclaw/skills/**" => python scripts/audit_cli_references.py --check --scope full
  - "docs/**" => python scripts/audit_cli_references.py --check --scope full
  - ".agents/skills/**" => python -m pytest tests/test_command_tree.py tests/test_voice_sidecar.py -q
  - ".claude/skills/**" => python -m pytest tests/test_command_tree.py tests/test_voice_sidecar.py -q
  - ".codex/agents/**" => python -m pytest tests/test_command_tree.py -q
  - ".claude/agents/**" => python -m pytest tests/test_command_tree.py -q
  - ".codex/config.toml" => python -m pytest tests/test_pre_submit_contract.py -q
  - ".mcp.json" => python -m pytest tests/test_pre_submit_contract.py -q
  - ".claude/gate-router.local.md" => python -m pytest tests/test_pre_submit_contract.py -q
  - ".gitattributes" => python -m pytest tests/test_pre_submit_contract.py -q
  - "skills/**" => python -m pytest tests/test_command_tree.py tests/test_voice_sidecar.py -q
  - "examples/openclaw/**" => python -m pytest tests/test_harness.py tests/test_openclaw_colo_smoke.py -q
  - "plugins/openclaw-anvil-intent-router/**" => cd plugins/openclaw-anvil-intent-router && npm test
  - "docs/**" => mkdocs build --strict
  - "docs/**/*.md" => python scripts/check_markdown_links.py --root .
---

Fast deterministic pre-ship gates derived from `.github/workflows/ci.yml`,
`.github/workflows/docs.yml`, and the repo-scoped agent/skill integration
surfaces. The full CI matrix and wheel smoke remain GitHub gates. This file is
forced to LF in `.gitattributes` because the gate-router frontmatter parser is
line-ending-sensitive.
