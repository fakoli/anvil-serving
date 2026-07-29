"""Documented CLI invocations must be runnable as written.

The capability guides show operators exact commands. A form that argparse rejects
(missing a required positional, or an option spelled as a positional) is worse than
no example, because it is copied straight into a terminal during an incident.

Two checks per documented invocation:

1. the command path exists in the CLI manifest; and
2. where the leaf's ``--help`` emits a machine-readable argparse ``usage:`` line,
   every required positional and required option in that line appears in the
   documented form.

Families that render help through a custom formatter (``router``) expose no usage
line, so only check 1 applies to them; the router transition forms are pinned by an
explicit test below instead.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "CLI-COMMAND-MANIFEST.json"

# The guides that show operator command forms.
DOC_PATHS = (
    Path("docs/MODEL-PROMOTION.md"),
    Path("docs/MODEL-LIFECYCLE.md"),
    Path("docs/PURPOSE-MODELS.md"),
)

_INVOCATION_RE = re.compile(r"^anvil-serving\s+(.+)$")
_USAGE_RE = re.compile(r"usage:\s*(.+?)(?:\n\n|\Z)", re.DOTALL)


def _command_paths() -> set[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {command["path"] for command in manifest["commands"]}


def _documented_invocations() -> list[tuple[str, str]]:
    """(source doc, invocation) for every fenced ``anvil-serving`` line."""
    found: list[tuple[str, str]] = []
    for doc in DOC_PATHS:
        in_fence = False
        for line in (ROOT / doc).read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            match = _INVOCATION_RE.match(line.strip())
            if match:
                found.append((doc.as_posix(), match.group(1)))
    return found


def _resolve(tokens: list[str], paths: set[str]) -> str | None:
    """Longest command path in `paths` that prefixes `tokens`."""
    for size in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:size])
        if candidate in paths:
            return candidate
    return None


def _usage_line(command: str) -> str | None:
    completed = subprocess.run(
        [sys.executable, "-m", "anvil_serving.cli", *command.split(), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    match = _USAGE_RE.search(completed.stdout)
    return " ".join(match.group(1).split()) if match else None


def test_documented_invocations_are_discoverable():
    """Guard the extractor itself, so an empty sweep cannot pass silently."""
    assert len(_documented_invocations()) >= 12


@pytest.mark.parametrize("doc, invocation", _documented_invocations())
def test_documented_command_path_exists(doc: str, invocation: str):
    tokens = [token for token in invocation.split() if not token.startswith("-")]
    resolved = _resolve(tokens, _command_paths())
    assert resolved, f"{doc}: '{invocation}' resolves to no command in the manifest"


@pytest.mark.parametrize("doc, invocation", _documented_invocations())
def test_documented_invocation_supplies_required_arguments(doc: str, invocation: str):
    tokens = invocation.split()
    bare = [token for token in tokens if not token.startswith("-")]
    command = _resolve(bare, _command_paths())
    assert command, f"{doc}: '{invocation}' resolves to no command"

    usage = _usage_line(command)
    if usage is None:
        pytest.skip(f"{command} renders help without an argparse usage line")

    tail = usage[usage.index(command) + len(command) :] if command in usage else usage
    # Required options appear unbracketed in an argparse usage line.
    for option in re.findall(r"(?<![\[\w-])(--[a-z][a-z0-9-]*)", tail):
        assert option in tokens, (
            f"{doc}: '{invocation}' omits required option {option}\n  usage: {usage}"
        )
    # Required positionals are bare uppercase tokens left after optional
    # `[...]` groups are removed. Strip innermost-first so nesting collapses.
    required = tail
    while True:
        collapsed = re.sub(r"\[[^\[\]]*\]", " ", required)
        if collapsed == required:
            break
        required = collapsed
    positionals = [t for t in required.split() if t.isupper() and t.isalpha()]
    if positionals:
        supplied = [t for t in bare if t not in command.split()]
        assert supplied, (
            f"{doc}: '{invocation}' omits required positional {positionals[0]}\n  usage: {usage}"
        )


def test_router_transition_forms_use_the_tier_option():
    """`--tier` is a required option, not a positional; drain also needs --timeout."""
    text = (ROOT / "docs" / "MODEL-PROMOTION.md").read_text(encoding="utf-8")
    for action in ("quiesce", "drain", "readmit"):
        line = next(
            (ln for ln in text.splitlines() if f"router {action}" in ln and "anvil-serving" in ln),
            None,
        )
        assert line, f"MODEL-PROMOTION.md no longer documents router {action}"
        assert "--tier" in line, f"router {action} documented without the required --tier"
    drain = next(ln for ln in text.splitlines() if "router drain" in ln and "anvil-serving" in ln)
    assert "--timeout" in drain, "router drain documented without the required --timeout"
