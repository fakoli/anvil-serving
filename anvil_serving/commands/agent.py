"""Command declarations for the Anvil Agent family."""

from .family import command_family
from .spec import CommandNode, _handler, _node, _option

_CONFIG = (_option("--config", value_name="PATH",
                   summary="Anvil Agent config TOML; defaults to $ANVIL_SERVING_HOME/agent.toml."),)


@command_family(category="Agent")
def commands() -> CommandNode:
    return _node(
        "agent",
        "Glasses turns to your own agents, and the memomind_glasses MCP tool.",
        children=(
            _node(
                "run",
                "Run the loopback Anvil Agent service in the foreground.",
                options=_CONFIG,
                handler=_handler("anvil_serving.agent.cli", argv_prefix=("run",)),
                mutation_class="process",
                output_policy="foreground",
            ),
            _node(
                "validate",
                "Check the Anvil Agent config and its references offline.",
                options=_CONFIG,
                handler=_handler("anvil_serving.agent.cli", argv_prefix=("validate",)),
            ),
        ),
        docs_anchor="docs/cli/agent.md",
    )
