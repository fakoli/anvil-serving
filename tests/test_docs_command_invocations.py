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
import shlex
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
    Path("docs/WORKLOAD-VISIBILITY.md"),
    Path("docs/cli/media.md"),
)

# Dispatcher-level options consumed before argparse; they take a value.
GLOBAL_VALUED_OPTIONS = frozenset(
    {
        "--target",
        "--transport",
        "--topology",
        "--topology-overlay",
        "--command-host",
        "--command-runtime",
    }
)

_INVOCATION_RE = re.compile(r"^anvil-serving\s+(.+)$")
_USAGE_RE = re.compile(r"usage:\s*(.+?)(?:\n\n|\Z)", re.DOTALL)


def _command_paths() -> set[str]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {command["path"] for command in manifest["commands"]}


def _documented_invocations() -> list[tuple[str, str]]:
    """Return each complete fenced ``anvil-serving`` shell invocation."""
    found: list[tuple[str, str]] = []
    for doc in DOC_PATHS:
        in_fence = False
        pending = ""
        for line in (ROOT / doc).read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("```"):
                assert not pending, f"{doc.as_posix()}: incomplete continued invocation"
                in_fence = not in_fence
                continue
            if not in_fence:
                continue
            stripped = line.strip()
            if pending:
                continued = stripped.endswith("\\")
                part = stripped[:-1].rstrip() if continued else stripped
                pending = f"{pending} {part}"
                if not continued:
                    found.append((doc.as_posix(), pending))
                    pending = ""
                continue
            match = _INVOCATION_RE.match(stripped)
            if match:
                invocation = match.group(1)
                if invocation.endswith("\\"):
                    pending = invocation[:-1].rstrip()
                else:
                    found.append((doc.as_posix(), invocation))
        assert not pending, f"{doc.as_posix()}: incomplete continued invocation"
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


def test_workload_guide_uses_explicit_source_ownership_options():
    paths = _command_paths()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    declared_options = {
        record["path"]: {
            flag for option in record["options"] for flag in option["flags"]
        }
        for record in manifest["commands"]
    }
    workload_invocations: dict[str, list[list[str]]] = {
        "router workloads": [],
        "fleet workloads": [],
        "dashboard serve": [],
    }
    for doc, invocation in _documented_invocations():
        if doc != "docs/WORKLOAD-VISIBILITY.md":
            continue
        tokens = shlex.split(invocation)
        resolved = _resolve(
            [token for token in tokens if not token.startswith("-")], paths
        )
        if resolved in workload_invocations:
            workload_invocations[resolved].append(tokens)

    expected = {
        "router workloads": {
            "--router-url": "http://127.0.0.1:8000/v1",
            "--auth-env": "ANVIL_WORKLOAD_TOKEN",
            "--expected-node": "node-a",
        },
        "fleet workloads": {
            "--controller-url": "http://127.0.0.1:8765",
            "--auth-env": "ANVIL_WORKLOAD_TOKEN",
            "--expected-node": "controller-a",
        },
        "dashboard serve": {
            "--workload-controller-url": "http://127.0.0.1:8765",
            "--workload-expected-node": "controller-a",
            "--workload-authorization-policy": (
                "/srv/anvil-serving/workload-authorization.json"
            ),
        },
    }
    resolution_flags = {
        *GLOBAL_VALUED_OPTIONS,
        "--allow-ssh-fallback",
        "--experimental-model-workload",
    }
    for command, required_options in expected.items():
        assert len(workload_invocations[command]) == 1, (
            f"workload guide must contain exactly one {command!r} example"
        )
        tokens = workload_invocations[command][0]
        if command == "dashboard serve":
            usage = _usage_line(command)
            assert usage is not None
            assert all(option in usage for option in required_options)
        else:
            assert required_options.keys() <= declared_options[command]
        for option, value in required_options.items():
            assert tokens.count(option) == 1
            assert tokens[tokens.index(option) + 1] == value
        if command in {"router workloads", "fleet workloads"}:
            assert resolution_flags.isdisjoint(tokens)


@pytest.mark.parametrize("doc, invocation", _documented_invocations())
def test_documented_command_path_exists(doc: str, invocation: str):
    tokens = [token for token in shlex.split(invocation) if not token.startswith("-")]
    resolved = _resolve(tokens, _command_paths())
    assert resolved, f"{doc}: '{invocation}' resolves to no command in the manifest"


@pytest.mark.parametrize("doc, invocation", _documented_invocations())
def test_documented_invocation_supplies_required_arguments(doc: str, invocation: str):
    tokens = shlex.split(invocation)
    bare = [token for token in tokens if not token.startswith("-")]
    command = _resolve(bare, _command_paths())
    assert command, f"{doc}: '{invocation}' resolves to no command"

    usage = _usage_line(command)
    if usage is None:
        pytest.skip(f"{command} renders help without an argparse usage line")

    tail = usage[usage.index(command) + len(command) :] if command in usage else usage

    # Options that take a value, so they can be skipped when counting positionals.
    # Dispatcher options are stripped before argparse sees them and so never appear
    # in a usage line; without them their values would count as positionals.
    valued = set(re.findall(r"(--[a-z][a-z0-9-]*) (?:[A-Za-z_][A-Za-z0-9_]*|\{[^}]*\})", tail))
    valued |= GLOBAL_VALUED_OPTIONS

    # Required options appear unbracketed in an argparse usage line.
    for option in re.findall(r"(?<![\[\w-])(--[a-z][a-z0-9-]*)", tail):
        assert option in tokens, (
            f"{doc}: '{invocation}' omits required option {option}\n  usage: {usage}"
        )

    # Required positionals are whatever is left after optional `[...]` groups are
    # removed. Metavars are not always uppercase (`repo_id`), so match on shape:
    # a bare token that is not an option and not an option's value.
    required = tail
    while True:
        collapsed = re.sub(r"\[[^\[\]]*\]", " ", required)
        if collapsed == required:
            break
        required = collapsed
    required_positionals: list[str] = []
    skip_next = False
    for token in required.split():
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            skip_next = token in valued
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            required_positionals.append(token)

    # Count what the documented form actually supplies, skipping option values.
    supplied: list[str] = []
    skip_next = False
    for token in tokens[len(command.split()) :]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            skip_next = token in valued
            continue
        supplied.append(token)

    # Optional positionals appear bracketed; count them so a legitimate form that
    # omits one still passes, while a surplus bare token (an option misspelled as a
    # positional, which argparse rejects) does not.
    optional_positionals = re.findall(r"\[([A-Za-z_][A-Za-z0-9_]*)(?: \.\.\.)?\]", tail)
    lower = len(required_positionals)
    # A variadic positional (`[names ...]`) accepts any number. Match the bracketed
    # form specifically so an ellipsis inside an option's metavar cannot silently
    # disable the upper bound.
    variadic = re.search(r"\[[A-Za-z_][A-Za-z0-9_]* \.\.\.\]", tail) is not None
    upper = float("inf") if variadic else lower + len(optional_positionals)
    assert lower <= len(supplied) <= upper, (
        f"{doc}: '{invocation}' supplies {len(supplied)} positional(s); "
        f"{command} accepts {lower}-{upper} "
        f"(required: {', '.join(required_positionals) or 'none'})\n  usage: {usage}"
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


def test_router_install_config_documents_its_required_option():
    """`router install-config` renders custom help, so pin --config explicitly."""
    text = (ROOT / "docs" / "MODEL-PROMOTION.md").read_text(encoding="utf-8")
    lines = [
        ln for ln in text.splitlines() if "router install-config" in ln and "anvil-serving" in ln
    ]
    assert lines, "MODEL-PROMOTION.md no longer documents router install-config"
    for line in lines:
        assert "--config" in line, f"install-config documented without --config: {line.strip()}"
