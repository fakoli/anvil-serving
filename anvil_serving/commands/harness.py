"""Command declarations for the harness family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _remote, _resource_node


@command_family(category="Control plane & integrations")
def commands() -> CommandNode:
    return _node(
        "harness",
        "Manage harness integration.",
        children=(
            _node(
                "sync",
                "Synchronize harness configuration",
                children=(
                    _resource_node(
                        "openclaw",
                        "Synchronize harness configuration for OpenClaw.",
                        "anvil_serving.harness",
                        role="gateway",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        remote_operation=_remote(
                            "openclaw_sync",
                            confirmed=(("confirm", True),),
                            allowed=(
                                "config",
                                "base_url",
                                "api_key_env",
                                "out",
                                "overwrite",
                                "voice",
                                "voice_realtime_url",
                                "voice_model",
                                "voice_api_key_env",
                                "dry_run",
                            ),
                        ),
                    ),
                ),
            ),
            _node(
                "restart",
                "Restart the harness",
                children=(
                    _resource_node(
                        "openclaw",
                        "Restart the harness for OpenClaw.",
                        "anvil_serving.harness",
                        role="gateway",
                        options=CONFIRM_OPTIONS,
                        mutation="mutate",
                        recovery=True,
                        remote_operation=_remote(
                            "openclaw_gateway_restart",
                            confirmed=(("confirm", True),),
                            allowed=("dry_run", "timeout_seconds"),
                        ),
                    ),
                ),
            ),
            _node(
                "status",
                "Show harness status",
                children=(
                    _resource_node(
                        "openclaw",
                        "Show harness status for OpenClaw.",
                        "anvil_serving.harness",
                        role="gateway",
                        remote_operation=_remote(
                            "openclaw_gateway_status",
                            allowed=("timeout_seconds", "max_output_bytes"),
                        ),
                    ),
                ),
            ),
        ),
        docs_anchor="docs/cli/control-plane.md#harness",
    )
