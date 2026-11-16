"""Claude Code must discover the same project skills and agents as Codex.

Codex discovers `.agents/skills` and `.codex/agents`; Claude Code discovers
`.claude/skills` and `.claude/agents`. Each Claude skill is a thin stub that
points at the canonical skill, so only names, descriptions, and pointers are
checked here.
"""

import re
import tomllib
from pathlib import Path

from anvil_serving.control_plane.mcp.tools import TOOLS

ROOT = Path(__file__).resolve().parents[1]


def _frontmatter(path: Path) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.S)
    assert match, f"{path} has no frontmatter"
    return dict(
        line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
    )


def _names(root: str) -> set[str]:
    return {skill.parent.name for skill in (ROOT / root).glob("*/SKILL.md")}


def test_claude_and_codex_discover_the_same_skills():
    assert _names(".claude/skills") == _names(".agents/skills")


def test_every_claude_skill_stub_points_at_its_canonical_skill():
    for name in _names(".agents/skills"):
        # `skills/` holds the full text when it exists; `.agents/skills` otherwise.
        canonical = ROOT / "skills" / name / "SKILL.md"
        if not canonical.exists():
            canonical = ROOT / ".agents" / "skills" / name / "SKILL.md"
        stub = ROOT / ".claude" / "skills" / name / "SKILL.md"
        assert _frontmatter(stub) == _frontmatter(canonical), name
        pointer = re.search(r"`(\.\./\.\./\.\./[^`]+/SKILL\.md)`", stub.read_text())
        assert pointer, f"{stub} does not point at its canonical skill"
        assert (stub.parent / pointer.group(1)).resolve() == canonical.resolve(), name


def test_every_codex_agent_has_a_claude_agent_with_its_mcp_tools():
    for codex in (ROOT / ".codex" / "agents").glob("*.toml"):
        role = tomllib.loads(codex.read_text())
        claude = ROOT / ".claude" / "agents" / f"{role['name']}.md"
        assert claude.exists(), f"Claude Code has no agent {role['name']}"
        meta = _frontmatter(claude)
        if "tools" not in meta:
            continue  # inherits every tool, including MCP
        tools = {tool.strip() for tool in meta["tools"].split(",")}
        allowed = re.search(r"Allowed tools:(.*?)(?:\n\n|Forbidden)", claude.read_text(), re.S)
        for token in re.findall(r"`([a-z][a-z0-9_]*\*?)`", allowed.group(1) if allowed else ""):
            names = [t for t in TOOLS if t.startswith(token[:-1])] if token.endswith("*") else [token]
            assert names, f"{claude.name} allows unknown tools {token}"
            for name in names:
                assert name in TOOLS, f"{claude.name} allows unknown tool {name}"
                assert f"mcp__anvil-serving__{name}" in tools, f"{claude.name} blocks {name}"
