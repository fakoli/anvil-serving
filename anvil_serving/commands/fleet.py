"""Command declarations for the fleet family."""

from .family import command_family
from .spec import CommandNode, _handler, _node, _option, _resource_node


@command_family(category="Fleet tools")
def commands() -> CommandNode:
    return _node(
        "fleet",
        "Cross-host visibility across the declared operator topology.",
        children=(
            _node(
                "workloads",
                "Read a bounded canonical workload snapshot from one fleet controller.",
                handler=_handler(
                    "anvil_serving.cli", attribute="_workload_command", argv_prefix=("fleet",)
                ),
                options=(
                    _option("--controller-url", summary="Explicit controller URL (required).", value_name="URL"),
                    _option("--auth-env", summary="Environment variable containing the controller credential (required).", value_name="NAME"),
                    _option("--expected-node", summary="Expected controller node identity (required).", value_name="NODE"),
                    _option("--owner", summary="Filter by workload owner.", value_name="OWNER"),
                    _option("--kind", summary="Filter by workload kind.", value_name="KIND"),
                    _option("--state", summary="Filter by workload state.", value_name="STATE"),
                    _option("--host", summary="Filter records by observed host.", value_name="HOST"),
                    _option("--active-only", summary="Return only active workloads."),
                    _option("--recent-seconds", summary="Recent-workload window in seconds.", value_name="SECONDS"),
                    _option("--limit", summary="Maximum returned workloads.", value_name="COUNT"),
                ),
            ),
            _resource_node(
                "version",
                "Report anvil-serving version skew across declared fleet hosts.",
                "anvil_serving.fleet",
                role="operator",
                handler_attribute="dispatch",
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
            _resource_node(
                "drift",
                "Compare each host's live operator home against its repository snapshot.",
                "anvil_serving.fleet",
                role="operator",
                options=(
                    _option(
                        "--repo",
                        summary="Private operator repository root (required).",
                        value_name="PATH",
                    ),
                    _option(
                        "--host",
                        summary="Repeatable host id; overrides repo-discovered hosts.",
                        value_name="NAME",
                    ),
                    _option(
                        "--home",
                        summary="Override the local live operator home.",
                        value_name="PATH",
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
