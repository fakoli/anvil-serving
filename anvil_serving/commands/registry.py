"""Deterministic assembly for the modular command registry."""

from __future__ import annotations

from dataclasses import replace

from .control_plane import commands as control_plane_commands
from .eval import commands as eval_commands
from .family import CommandFamily
from .fleet import commands as fleet_commands
from .harness import commands as harness_commands
from .host import commands as host_commands
from .models import commands as model_commands
from .media import commands as media_commands
from .router import commands as router_commands
from .serves import commands as serves_commands
from .voice import commands as voice_commands

from .spec import (
    GLOBAL_OPTIONS,
    CommandNode,
    CommandTree,
    _inherit_docs_anchor,
    validate_command_tree,
)


ROOT_ORDER = (
    "init",
    "router",
    "fleet",
    "serves",
    "models",
    "eval",
    "voice",
    "media",
    "harness",
    "mcp",
    "controller",
    "host",
    "doctor",
    "upgrade",
    "topology",
    "collectors",
    "dashboard",
    "edge",
    "workbench",
)


def build_command_tree(
    families: tuple[CommandFamily, ...] | None = None,
) -> CommandTree:
    """Build the canonical tree from an explicit, deterministic family list."""

    selected = FAMILIES if families is None else families
    roots: dict[str, CommandNode] = {}
    for family in selected:
        for declaration in family.build():
            node = replace(
                _inherit_docs_anchor(declaration),
                group=family.category,
            )
            if node.name in roots:
                raise ValueError(f"duplicate command family root {node.name!r}")
            roots[node.name] = node

    missing = set(ROOT_ORDER) - roots.keys()
    unexpected = roots.keys() - set(ROOT_ORDER)
    if missing or unexpected:
        raise ValueError(
            "command family roots do not match the public surface: "
            f"missing={sorted(missing)} unexpected={sorted(unexpected)}"
        )
    tree = CommandTree(
        nodes=tuple(roots[name] for name in ROOT_ORDER),
        global_options=GLOBAL_OPTIONS,
    )
    validate_command_tree(tree, resolve_handlers=False)
    return tree


# This explicit list is the registration boundary. There is no filesystem
# discovery, and decorator evaluation imports no command handlers.
FAMILIES = (
    host_commands,
    router_commands,
    fleet_commands,
    serves_commands,
    model_commands,
    eval_commands,
    voice_commands,
    media_commands,
    harness_commands,
    control_plane_commands,
)
COMMAND_TREE = build_command_tree(FAMILIES)
