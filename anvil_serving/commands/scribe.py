"""Command declarations for the Anvil Scribe family."""

from .family import command_family
from .spec import CommandNode, _handler, _node, _option

_CONFIG = (_option("--config", value_name="PATH",
                   summary="Scribe config TOML; defaults to $ANVIL_SERVING_HOME/scribe.toml."),)


@command_family(category="Scribe")
def commands() -> CommandNode:
    return _node(
        "scribe",
        "Record-first dictation: raw transcripts, saved rewrite profiles, history.",
        children=(
            _node(
                "run",
                "Run the loopback Scribe service in the foreground.",
                options=_CONFIG,
                handler=_handler("anvil_serving.scribe.cli", argv_prefix=("run",)),
                mutation_class="process",
                output_policy="foreground",
            ),
            _node(
                "validate",
                "Check the Scribe config and its references offline.",
                options=_CONFIG,
                handler=_handler("anvil_serving.scribe.cli", argv_prefix=("validate",)),
            ),
        ),
        docs_anchor="docs/cli/scribe.md",
    )
