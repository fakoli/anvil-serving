"""The pre-submit contract must itself be enforced.

scripts/pre-submit.sh is the single command a contributor/agent runs before
`anvil submit` or opening a PR. If it goes missing, loses its executable bit, or
stops running the two invariants it exists to guard (regenerating the CLI
reference audit and running the full suite), the contract is silently broken.
These checks keep the tool honest — and are hermetic (no subprocess, no shell).
"""

from __future__ import annotations

from pathlib import Path

from anvil_serving.client_catalog_sync import HERMES_MEDIA_TOOLS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pre-submit.sh"
GATE_CONFIG = ROOT / ".claude" / "gate-router.local.md"
ATTRIBUTES = ROOT / ".gitattributes"
CODEX_MCP_CONFIG = ROOT / ".codex" / "config.toml"
CLAUDE_MCP_CONFIG = ROOT / ".mcp.json"
PACKAGED_HERMES_MEDIA_SKILL = (
    ROOT / "anvil_serving" / "_hermes_skills" / "anvil-media" / "SKILL.md"
)
EXAMPLE_HERMES_MEDIA_SKILL = (
    ROOT / "examples" / "hermes" / "skills" / "anvil-media" / "SKILL.md"
)


def test_hermes_media_skill_is_lf_stable_and_byte_synchronized():
    packaged = PACKAGED_HERMES_MEDIA_SKILL.read_bytes()
    example = EXAMPLE_HERMES_MEDIA_SKILL.read_bytes()

    assert b"\r\n" not in packaged
    assert packaged.startswith(b"---\nname: anvil-media\n")
    assert example == packaged
    text = packaged.decode("utf-8")
    referenced = {
        token.removeprefix("mcp__anvil_media__")
        for token in text.replace("`", " ").split()
        if token.startswith("mcp__anvil_media__media_")
    }
    assert referenced == set(HERMES_MEDIA_TOOLS)
    assert "media_worker_" not in text

    attributes = ATTRIBUTES.read_text(encoding="utf-8")
    assert "anvil_serving/_hermes_skills/** text eol=lf" in attributes
    assert "examples/hermes/skills/** text eol=lf" in attributes
