"""Claude Code must discover the same project skills and agents as Codex.

Codex discovers `.agents/skills` and `.codex/agents`; Claude Code discovers
`.claude/skills` and `.claude/agents`. Each Claude skill is a thin stub that
points at the canonical skill, so only names, descriptions, and pointers are
checked here.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _frontmatter(path: Path) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.S)
    assert match, f"{path} has no frontmatter"
    return dict(
        line.split(":", 1) for line in match.group(1).splitlines() if ":" in line
    )


def _canonical_skills() -> dict[str, Path]:
    found: dict[str, Path] = {}
    # `skills/` wins over `.agents/skills`, whose shared entries are stubs.
    for root in (".agents/skills", "skills"):
        for skill in (ROOT / root).glob("*/SKILL.md"):
            found[skill.parent.name] = skill
    return found


def test_every_canonical_skill_has_a_claude_stub():
    for name, canonical in _canonical_skills().items():
        stub = ROOT / ".claude" / "skills" / name / "SKILL.md"
        assert stub.exists(), f"Claude Code cannot discover skill {name}"
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
        for name in re.findall(r"`([a-z][a-z0-9_]+)`", allowed.group(1) if allowed else ""):
            assert f"mcp__anvil-serving__{name}" in tools, f"{claude.name} blocks {name}"
