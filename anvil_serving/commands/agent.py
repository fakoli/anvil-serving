"""Command declarations for the Anvil Agent family."""

from .family import command_family
from .spec import CommandNode, _handler, _node


@command_family(category="Agent")
def commands() -> CommandNode:
    return _node(
        "agent",
        "Glasses turns to your own agents, and the memomind_glasses MCP tool.",
        children=(
            _node(
                "run",
                "Run the loopback Anvil Agent service in the foreground.",
                handler=_handler("anvil_serving.agent.cli", argv_prefix=("run",)),
                mutation_class="process",
                output_policy="foreground",
            ),
            _node(
                "validate",
                "Check the Anvil Agent config and its references offline.",
                handler=_handler("anvil_serving.agent.cli", argv_prefix=("validate",)),
            ),
        ),
        docs_anchor="docs/cli/agent.md",
    )
