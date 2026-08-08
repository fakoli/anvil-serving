"""Command declarations for the fleet family."""

from .family import command_family
from .spec import CommandNode, _node, _option, _resource_node


@command_family(category="Fleet tools")
def commands() -> CommandNode:
    return _node(
        "fleet",
        "Cross-host visibility across the declared operator topology.",
        children=(
            _resource_node(
                "version",
                "Report anvil-serving version skew across declared fleet hosts.",
                "anvil_serving.fleet",
                role="operator",
                options=(
                    _option(
                        "--host",
                        summary="Repeatable declared host id; overrides topology-derived hosts.",
                        value_name="NAME",
                    ),
                    _option(
                        "--timeout",
                        summary="Per-host SSH probe timeout (default: 10s).",
                        value_name="SECONDS",
                    ),
                ),
            ),
        ),
        docs_anchor="docs/cli/fleet.md",
    )
