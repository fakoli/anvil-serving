"""Command declarations for the router family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _handler, _node, _option, _remote, _resource_node


@command_family(category="Data plane")
def commands() -> CommandNode:
    return _node(
        "router",
        "Manage the deployed router and its lifecycle.",
        children=(
            _node(
                "diagnose",
                "Explain one request from bounded router evidence without replaying it.",
                handler=_handler("anvil_serving.router_diagnostics", attribute="dispatch", argv_prefix=()),
                options=(
                    _option("--request-id", summary="Request identifier returned by the gateway.", value_name="ID"),
                    _option("--router-url", summary="Explicit router HTTP(S) origin.", value_name="URL"),
                    _option("--auth-env", summary="Environment variable containing the router credential.", value_name="NAME"),
                    _option("--timeout", summary="Per-read socket timeout, at most 30 seconds.", value_name="SECONDS"),
                ),
                docs_anchor="docs/cli/router.md#diagnose",
            ),
            _resource_node(
                "run",
                "Run the router in the foreground.",
                "anvil_serving.router.serve",
                role="router",
                mutation="process",
                argv_prefix=(),
                output_policy="foreground",
                options=(
                    _option(
                        "--config",
                        summary="Direct capability-gateway TOML; defaults to the operator config home.",
                        value_name="PATH",
                    ),
                    _option("--host", summary="Router bind host.", value_name="ADDRESS"),
                    _option("--port", summary="Router bind port.", value_name="PORT"),
                ),
            ),
            _resource_node(
                "up",
                "Start the deployed router.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--compose", summary="Router Docker Compose file.", value_name="PATH"),
                    _option("--service", summary="Router Compose service.", value_name="NAME"),
                    _option(
                        "--env-file", summary="Router Compose environment file.", value_name="PATH"
                    ),
                    _option("--recreate", summary="Force-recreate only the router service."),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_manage",
                    fixed=(("action", "up"),),
                    allowed=("compose", "service", "env_file", "recreate", "dry_run"),
                ),
            ),
            _resource_node(
                "down",
                "Stop the deployed router.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--compose", summary="Router Docker Compose file.", value_name="PATH"),
                    _option("--service", summary="Router Compose service.", value_name="NAME"),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_manage",
                    fixed=(("action", "down"),),
                    allowed=("compose", "service", "dry_run"),
                ),
            ),
            _resource_node(
                "restart",
                "Restart the deployed router.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--container", summary="Deployed router container.", value_name="NAME"),
                    _option("--no-verify", summary="Skip post-restart container verification."),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_manage",
                    fixed=(("action", "restart"),),
                    allowed=("container", "dry_run", "no_verify"),
                ),
            ),
            _resource_node(
                "reload",
                "Reload router configuration.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--container", summary="Deployed router container.", value_name="NAME"),
                    _option("--no-verify", summary="Skip post-restart container verification."),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_manage",
                    fixed=(("action", "reload"),),
                    allowed=("container", "dry_run", "no_verify"),
                ),
            ),
            _resource_node(
                "install-config",
                "Validate and atomically install a router config, including tier-set migrations.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--config", summary="Complete direct router config.", value_name="PATH"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                    _option(
                        "--drain-timeout",
                        summary="Per-tier bounded drain timeout.",
                        value_name="SECONDS",
                    ),
                ),
                mutation="mutate",
            ),
            _resource_node(
                "endpoint",
                "Show the router listen address and this node's Tailscale DNS name.",
                "anvil_serving.router_endpoint",
                role="router",
                argv_prefix=(),
                execution_runtime_roles=("native",),
            ),
            _resource_node(
                "status",
                "Show router status.",
                "anvil_serving.router_manage",
                role="router",
                remote_operation=_remote("router_status", allowed=("container",)),
            ),
            _resource_node(
                "fleet-status",
                "Report which configured capabilities have a reachable backing serve.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option(
                        "--config",
                        summary="Inspect one router config file instead of the installed router.",
                        value_name="PATH",
                    ),
                    _option(
                        "--live",
                        summary="Probe the installed config from the live router runtime (default).",
                    ),
                    _option("--container", summary="Deployed router container.", value_name="NAME"),
                    _option(
                        "--installed-config",
                        summary="Config path inside the deployed router container.",
                        value_name="PATH",
                    ),
                    _option(
                        "--probe-perspective",
                        summary="Execution perspective for explicit config inspection.",
                        value_name="PERSPECTIVE",
                    ),
                    _option("--timeout", summary="Per-endpoint probe timeout (s).", value_name="SECONDS"),
                ),
                remote_operation=_remote(
                    "router_fleet_status", allowed=("timeout",)
                ),
                docs_anchor="docs/cli/router.md#fleet-status",
            ),
            _resource_node(
                "transition-status",
                "Show router tier transition state.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option("--tier", summary="Optional tier id.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "status"),),
                    allowed=("tier", "router_url"),
                ),
            ),
            _resource_node(
                "quiesce",
                "Quiesce one router tier.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "quiesce"),),
                    allowed=("tier", "router_url", "timeout", "dry_run"),
                ),
            ),
            _resource_node(
                "drain",
                "Wait for a quiesced tier to drain.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                    _option("--timeout", summary="Positive drain timeout.", value_name="SECONDS"),
                ),
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "drain"),),
                    allowed=("tier", "router_url", "timeout", "dry_run"),
                ),
            ),
            _resource_node(
                "readmit",
                "Safely readmit one router tier.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "readmit"),),
                    allowed=("tier", "router_url", "timeout", "dry_run"),
                ),
            ),
            _resource_node(
                "logs",
                "Read bounded router logs.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option("--follow", summary="Follow log output.", output_policy="follow"),
                ),
                remote_operation=_remote(
                    "router_logs", allowed=("container", "tail", "since", "follow")
                ),
            ),
            _resource_node(
                "token",
                "Inspect the router token state.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option(
                        "--reveal",
                        summary="Reveal the local token after confirmation.",
                        requires_confirmation=True,
                    ),
                    _option("--confirm", summary="Confirm token reveal."),
                ),
            ),
        ),
        docs_anchor="docs/cli/router.md",
    )
