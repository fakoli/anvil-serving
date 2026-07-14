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
  - "docs/**" => python scripts/audit_cli_references.py --check --scope full
  - "docs/**" => mkdocs build --strict
  - "docs/**/*.md" => python scripts/check_markdown_links.py --root .
---

Fast deterministic pre-ship gates derived from `.github/workflows/ci.yml` and
`.github/workflows/docs.yml`. The full CI matrix and wheel smoke remain GitHub gates.
