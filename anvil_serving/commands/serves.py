"""Command declarations for the serves family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _option, _remote, _resource_node


@command_family(category="Local serving tools")
def commands() -> CommandNode:
    return _node(
        "serves",
        "Manage local model serve lifecycle.",
        children=(
            _resource_node(
                "render",
                "Render a model serve definition.",
                "anvil_serving.serves",
                role="model-serve",
                mutation="mutate",
                gpu=True,
            ),
            _resource_node(
                "up",
                "Start manifest-owned model serves.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "up"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "down",
                "Stop manifest-owned model serves.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "down"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "rm",
                "Remove a model serve.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "rm"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "adopt",
                "Adopt an existing model serve.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_manage", fixed=(("action", "adopt"),), positionals=("names",)
                ),
            ),
            _resource_node(
                "switch",
                "Switch a deployment role to an activation-ready recipe.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS
                + (
                    _option("--manifest", summary="Serve manifest TOML.", value_name="PATH"),
                    _option("--registry", summary="Serve-recipe registry TOML.", value_name="PATH"),
                    _option(
                        "--recipe",
                        summary="Compatibility spelling for the positional MODEL selector.",
                        value_name="MODEL",
                        requires_confirmation=True,
                    ),
                ),
                mutation="mutate",
                gpu=True,
            ),
            _resource_node(
                "promote",
                "Promote a staged model recipe with preflight and full rollback.",
                "anvil_serving.serves",
                role="model-serve",
                options=CONFIRM_OPTIONS,
                mutation="mutate",
                gpu=True,
                remote_operation=_remote(
                    "serves_promote",
                    confirmed=(("human_approved", True),),
                    allowed=("manifest", "rollback", "resume", "dry_run"),
                    positionals=("plan",),
                ),
            ),
            _resource_node(
                "status",
                "Show model serve status.",
                "anvil_serving.serves",
                role="model-serve",
                gpu=True,
                remote_operation=_remote("serves_status", positionals=("names",)),
            ),
            _resource_node(
                "groups",
                "List serve groups across the manifest set and their members.",
                "anvil_serving.serves",
                role="model-serve",
            ),
            _resource_node(
                "logs",
                "Read bounded model serve logs.",
                "anvil_serving.serves",
                role="model-serve",
                options=(
                    _option("--follow", summary="Follow log output.", output_policy="follow"),
                ),
                gpu=True,
                remote_operation=_remote("serves_logs", positionals=("names",)),
            ),
            _resource_node(
                "multiplex",
                "Run the single-resident model multiplexer.",
                "anvil_serving.multiplexer",
                role="model-serve",
                mutation="process",
                gpu=True,
                argv_prefix=(),
                output_policy="foreground",
            ),
        ),
        docs_anchor="docs/cli/serves.md",
    )
