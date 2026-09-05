"""Command declarations for the control plane family."""

from .family import command_family
from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _handler, _option, _remote, _resource_node


@command_family(category="Control plane & integrations")
def commands() -> tuple[CommandNode, ...]:
    return (
        _node(
            "mcp",
            "Expose bounded MCP management tools.",
            children=(
                _resource_node(
                    "serve",
                    "Run the MCP management server.",
                    "anvil_serving.mcp",
                    role="operator",
                    argv_prefix=(),
                    output_policy="protocol",
                    remote_operation=_remote(mode="mcp-bridge"),
                ),
                _resource_node(
                    "tools",
                    "List bounded MCP tools.",
                    "anvil_serving.mcp",
                    role="operator",
                    argv_prefix=("list-tools",),
                    remote_operation=_remote(mode="mcp-bridge"),
                ),
            ),
            docs_anchor="docs/cli/control-plane.md#mcp",
        ),
        _node(
            "controller",
            "Manage the private controller service.",
            children=(
                _resource_node(
                    "serve",
                    "Run the private controller.",
                    "anvil_serving.controller",
                    role="controller",
                    mutation="process",
                    output_policy="foreground",
                ),
                _resource_node(
                    "status",
                    "Probe controller health.",
                    "anvil_serving.controller",
                    role="controller",
                    remote_operation=_remote(mode="controller-status"),
                ),
                _resource_node(
                    "inspect",
                    "Read-only metadata inspection for one controller container.",
                    "anvil_serving.controller_diagnostics",
                    role="controller",
                    options=(
                        _option(
                            "--container",
                            summary="Required local controller Docker name or immutable ID.",
                            value_name="NAME",
                        ),
                    ),
                    argv_prefix=("inspect",),
                    remote_operation=_remote(
                        "controller_inspect",
                        allowed=("container",),
                    ),
                ),
                _resource_node(
                    "logs",
                    "Read bounded metadata-only controller audit events.",
                    "anvil_serving.controller_diagnostics",
                    role="controller",
                    options=(
                        _option(
                            "--container",
                            summary="Required local controller Docker name or immutable ID.",
                            value_name="NAME",
                        ),
                        _option(
                            "--tail",
                            summary="Audit event tail in the fixed range 1..200.",
                            value_name="N",
                        ),
                    ),
                    argv_prefix=("logs",),
                    remote_operation=_remote(
                        "controller_logs",
                        allowed=("container", "tail"),
                    ),
                ),
            ),
            docs_anchor="docs/cli/control-plane.md#controller",
        ),
        _node(
            "topology",
            "Inspect and resolve deployment topology.",
            children=(
                _node(
                    "show",
                    "Show a validated topology summary.",
                    handler=_handler("anvil_serving.topology_cli", argv_prefix=("show",)),
                ),
                _node(
                    "validate",
                    "Validate a topology offline.",
                    handler=_handler("anvil_serving.topology_cli", argv_prefix=("validate",)),
                ),
                _node(
                    "resolve",
                    "Resolve one canonical command against a topology.",
                    handler=_handler("anvil_serving.topology_cli", argv_prefix=("resolve",)),
                ),
                _node(
                    "drift",
                    "Compare the installed topology against a canonical fleet reference.",
                    handler=_handler("anvil_serving.topology_cli", argv_prefix=("drift",)),
                ),
                _node(
                    "validate-router-config",
                    "Validate a router config against declared topology offline.",
                    options=(
                        _option(
                            "--config",
                            summary="Required router configuration TOML.",
                            value_name="PATH",
                        ),
                    ),
                    handler=_handler(
                        "anvil_serving.topology_cli",
                        argv_prefix=("validate-router-config",),
                    ),
                ),
            ),
            docs_anchor="docs/cli/control-plane.md#topology",
        ),
        _node(
            "collectors",
            "Configure and inspect optional read-only collector adapters.",
            children=(
                _node(
                    "configure",
                    "Validate and optionally write adapter configuration.",
                    options=(
                        _option(
                            "--output",
                            summary="Write validated configuration.",
                            value_name="PATH",
                            requires_confirmation=True,
                        ),
                        _option("--confirm", summary="Confirm writing collector configuration."),
                    ),
                    handler=_handler("anvil_serving.collectors", argv_prefix=("configure",)),
                    mutation_class="mutate",
                ),
                _node(
                    "validate",
                    "Validate adapter configuration without network access.",
                    handler=_handler("anvil_serving.collectors", argv_prefix=("validate",)),
                ),
                _node(
                    "capabilities",
                    "Report configured adapter capabilities offline.",
                    handler=_handler("anvil_serving.collectors", argv_prefix=("capabilities",)),
                ),
                _node(
                    "inspect",
                    "Perform one bounded read-only adapter inspection.",
                    handler=_handler("anvil_serving.collectors", argv_prefix=("inspect",)),
                ),
            ),
            docs_anchor="docs/cli/control-plane.md#collectors",
        ),
        _node(
            "edge",
            "Own the Tailscale tailnet edge in front of the unchanged router.",
            children=(
                _node(
                    "bundle",
                    "Plan a portable tailnet model endpoint offline.",
                    children=(
                        _resource_node(
                            "validate",
                            "Validate a portable endpoint manifest offline.",
                            "anvil_serving.edge_bundle",
                            role="host",
                            argv_prefix=("validate",),
                            options=(_option("--manifest", summary="Required endpoint JSON manifest.", value_name="PATH"),),
                            execution_runtime_roles=("native",),
                            docs_anchor="docs/REMOTE-TAILNET-ENDPOINTS.md#validate-and-render",
                        ),
                        _resource_node(
                            "render",
                            "Render Compose, Serve, and router fragments without applying.",
                            "anvil_serving.edge_bundle",
                            role="host",
                            argv_prefix=("render",),
                            options=(_option("--manifest", summary="Required endpoint JSON manifest.", value_name="PATH"),),
                            execution_runtime_roles=("native",),
                            docs_anchor="docs/REMOTE-TAILNET-ENDPOINTS.md#validate-and-render",
                        ),
                    ),
                    docs_anchor="docs/REMOTE-TAILNET-ENDPOINTS.md",
                ),
                _resource_node(
                    "render",
                    "Render the tailscale serve invocations without applying.",
                    "anvil_serving.edge",
                    role="host",
                    argv_prefix=("render",),
                    execution_runtime_roles=("native",),
                ),
                _resource_node(
                    "status",
                    "Show serve mappings, flagging which this tool manages.",
                    "anvil_serving.edge",
                    role="host",
                    argv_prefix=("status",),
                    execution_runtime_roles=("native",),
                ),
                _resource_node(
                    "up",
                    "Apply the managed route map (additive; idempotent).",
                    "anvil_serving.edge",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    argv_prefix=("up",),
                    execution_runtime_roles=("native",),
                ),
                _resource_node(
                    "down",
                    "Remove ONLY the mounts this tool manages.",
                    "anvil_serving.edge",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    argv_prefix=("down",),
                    execution_runtime_roles=("native",),
                ),
            ),
            docs_anchor="docs/cli/control-plane.md#edge",
        ),
        _node(
            "workbench",
            "Manage the optional private Anvil Workbench hub stack.",
            children=(
                _resource_node(
                    "build",
                    "Build the Workbench hub image from the local companion checkout.",
                    "anvil_serving.workbench",
                    role="host",
                    options=CONFIRM_OPTIONS
                    + (
                        _option("--source", summary="Workbench source checkout.", value_name="PATH"),
                        _option("--image", summary="Local image tag.", value_name="TAG"),
                    ),
                    mutation="mutate",
                    argv_prefix=("build",),
                    docs_anchor="docs/WORKBENCH.md#lifecycle",
                ),
                _resource_node(
                    "up",
                    "Start the private Workbench hub, Postgres, and Neo4j projection.",
                    "anvil_serving.workbench",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    argv_prefix=("up",),
                    docs_anchor="docs/WORKBENCH.md#lifecycle",
                ),
                _resource_node(
                    "down",
                    "Stop the Workbench hub stack while preserving its named data volumes.",
                    "anvil_serving.workbench",
                    role="host",
                    options=CONFIRM_OPTIONS,
                    mutation="mutate",
                    argv_prefix=("down",),
                    docs_anchor="docs/WORKBENCH.md#lifecycle",
                ),
                _resource_node(
                    "status",
                    "Show the bounded Docker Compose service status for Workbench.",
                    "anvil_serving.workbench",
                    role="host",
                    argv_prefix=("status",),
                    docs_anchor="docs/WORKBENCH.md#lifecycle",
                ),
                _resource_node(
                    "logs",
                    "Read bounded Workbench hub stack logs.",
                    "anvil_serving.workbench",
                    role="host",
                    argv_prefix=("logs",),
                    docs_anchor="docs/WORKBENCH.md#lifecycle",
                ),
            ),
            docs_anchor="docs/WORKBENCH.md",
        ),
    )
