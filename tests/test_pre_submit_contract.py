"""The pre-submit contract must itself be enforced.

scripts/pre-submit.sh is the single command a contributor/agent runs before
`anvil submit` or opening a PR. If it goes missing, loses its executable bit, or
stops running the two invariants it exists to guard (regenerating the CLI
reference audit and running the full suite), the contract is silently broken.
These checks keep the tool honest — and are hermetic (no subprocess, no shell).
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre-submit.sh"
GATE_CONFIG = ROOT / ".claude" / "gate-router.local.md"
ATTRIBUTES = ROOT / ".gitattributes"
CODEX_MCP_CONFIG = ROOT / ".codex" / "config.toml"
CLAUDE_MCP_CONFIG = ROOT / ".mcp.json"


def test_pre_submit_script_exists_and_is_well_formed():
    assert SCRIPT.is_file(), "scripts/pre-submit.sh must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!"), "script must start with a shebang"
    assert "/sh" in text.splitlines()[0], "script must be POSIX sh"
    # It must run the two invariants it exists to guard.
    assert "scripts/audit_cli_references.py --update" in text
    assert "pytest tests/" in text
    # Regenerating and then checking the tree is dirty is the whole point.
    assert "git diff --quiet" in text
    # It must be able to fail the caller.
    assert "exit 1" in text


def test_pre_submit_script_is_executable_in_the_git_index():
    """Executable bit is tracked in git so it holds on Windows and Linux alike."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/pre-submit.sh"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert listing, "scripts/pre-submit.sh must be tracked by git"
    mode = listing.split()[0]
    assert mode == "100755", f"script must be executable (mode 100755), got {mode}"


def test_gate_router_config_is_parseable_on_windows_and_covers_agent_surfaces():
    """The plugin's POSIX frontmatter parser treats CRLF fences as missing."""
    raw = GATE_CONFIG.read_bytes()
    assert b"\r\n" not in raw, "gate-router config must stay LF-only"

    text = raw.decode("utf-8")
    assert text.startswith("---\n")
    assert "\n---\n" in text
    rules = [
        line.strip()[2:]
        for line in text.splitlines()[1:]
        if line.strip().startswith("- ")
    ]
    assert rules
    assert all(" => " in rule for rule in rules)

    for surface in (
        '".agents/skills/**"',
        '".claude/skills/**"',
        '".codex/agents/**"',
        '".claude/agents/**"',
        '".codex/config.toml"',
        '".mcp.json"',
        '".claude/gate-router.local.md"',
        '".gitattributes"',
        '"skills/**"',
        '"examples/openclaw/**"',
        '"plugins/openclaw-anvil-intent-router/**"',
    ):
        assert any(rule.startswith(surface) for rule in rules)

    attributes = ATTRIBUTES.read_text(encoding="utf-8")
    assert ".claude/gate-router.local.md text eol=lf" in attributes


def test_repo_harnesses_register_the_checkout_mcp_server():
    codex = tomllib.loads(CODEX_MCP_CONFIG.read_text(encoding="utf-8"))
    codex_server = codex["mcp_servers"]["anvil-serving"]
    assert codex_server["command"] == "python"
    assert codex_server["args"] == ["-m", "anvil_serving.cli", "mcp", "serve"]
    assert codex_server["cwd"] == "."
    imported = subprocess.check_output(
        [
            codex_server["command"],
            "-c",
            "import pathlib, anvil_serving; print(pathlib.Path(anvil_serving.__file__).resolve())",
        ],
        cwd=ROOT / codex_server["cwd"],
        text=True,
    ).strip()
    assert Path(imported).is_relative_to(ROOT)

    claude = json.loads(CLAUDE_MCP_CONFIG.read_text(encoding="utf-8"))
    claude_server = claude["mcpServers"]["anvil-serving"]
    assert claude_server["type"] == "stdio"
    assert claude_server["command"] == "python"
    assert claude_server["args"] == ["-m", "anvil_serving.cli", "mcp", "serve"]


def test_repo_voice_skill_is_discoverable_without_copying_the_canonical_body():
    canonical = ROOT / "skills" / "anvil-serving-voice-ops" / "SKILL.md"
    assert canonical.is_file()
    for wrapper in (
        ROOT / ".agents" / "skills" / "anvil-serving-voice-ops" / "SKILL.md",
        ROOT / ".claude" / "skills" / "anvil-serving-voice-ops" / "SKILL.md",
    ):
        text = wrapper.read_text(encoding="utf-8")
        assert "name: anvil-serving-voice-ops" in text
        assert "../../../skills/anvil-serving-voice-ops/SKILL.md" in text
