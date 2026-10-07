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
            _resource_node(
                "export-config",
                "Export the verified, secret-free configuration mounted by the running router.",
                "anvil_serving.router_config_export",
                role="router",
                argv_prefix=(),
                handler_attribute="dispatch",
                execution_runtime_roles=("native",),
                options=(
                    _option("--container", summary="Exact running router container.", value_name="NAME"),
                    _option("--expected-sha256", summary="Required exact source and mounted-file SHA-256.", value_name="SHA256"),
                ),
                docs_anchor="docs/cli/router.md#export-installed-configuration",
            ),
            _node(
                "keys",
                "Manage local device API keys and inspect bounded access history.",
                children=tuple(
                    _node(
                        action, summary,
                        handler=_handler("anvil_serving.router.keys", attribute="dispatch", argv_prefix=(action,)),
                        mutation_class="mutate" if action in {"init", "create", "revoke", "bind", "backup", "restore"} else "read",
                        options=(
                            _option("--config", summary="Router config declaring server.api_keys_path.", value_name="PATH"),
                            _option("--container", summary="Run storage operations as the verified router container user; secret output stays on this host.", value_name="NAME"),
                        ) + options,
                        docs_anchor="docs/cli/router.md#device-api-keys",
                    )
                    for action, summary, options in (
                        ("init", "Initialize protected device-key storage without changing the master key.", ()),
                        ("create", "Create a scoped device key and save its secret once to a protected file.", (
                            _option("--name", summary="Device label (required).", value_name="NAME"),
                            _option("--model", summary="Allowed alias or purpose-model name; repeat for multiple grants (required).", value_name="MODEL"),
                            _option("--path", summary="Allowed inference endpoint; repeat for multiple grants (required).", value_name="PATH"),
                            _option("--rpm", summary="Request token bucket capacity and refill per minute (default 60).", value_name="COUNT"),
                            _option("--expires-days", summary="Expire the key after this many days.", value_name="DAYS"),
                            _option("--out", summary="New protected secret file (required); never overwritten.", value_name="PATH"),
                        )),
                        ("list", "List key IDs, grants, and lifecycle state without secrets.", ()),
                        ("revoke", "Revoke a device key for subsequent requests.", (
                            _option("--key-id", summary="Device key ID to revoke (required).", value_name="ID"),
                        )),
                        ("bind", "Bind an ordinary key to an operator-controlled owner with revision CAS.", (
                            _option("--key-id", summary="Ordinary key ID (required).", value_name="ID"),
                            _option("--kind", summary="human or service (required).", value_name="KIND"),
                            _option("--owner-id", summary="Trusted opaque owner ID (required).", value_name="ID"),
                            _option("--expected-revision", summary="Current binding revision; zero for unbound (required).", value_name="COUNT"),
                            _option("--dry-run", summary="Validate without changing binding."),
                        )),
                        ("backup", "Take a consistent protected key/accounting snapshot.", (
                            _option("--out", summary="Absent protected destination (required).", value_name="PATH"),
                        )),
                        ("restore", "Restore a protected snapshot to an absent destination without activation.", (
                            _option("--snapshot", summary="Protected source snapshot (required).", value_name="PATH"),
                            _option("--out", summary="Absent protected destination (required).", value_name="PATH"),
                        )),
                        ("usage", "Read bounded key access history.", (
                            _option("--key-id", summary="Filter history to one key ID; _legacy selects the master.", value_name="ID"),
                            _option("--limit", summary="Maximum recent request records (default 50).", value_name="COUNT"),
                        )),
                    )
                ),
            ),
            _node(
                "usage", "Read protected exact retained or active caller accounting.",
                children=tuple(_node(
                    action, summary,
                    handler=_handler("anvil_serving.router_diagnostics", attribute="dispatch_usage", argv_prefix=(action,)),
                    options=tuple(_option(name, summary=help, value_name=value) for name, help, value in (
                        ("--config", "Saved router-diagnostics.toml; defaults to operator home.", "PATH"),
                        ("--router-url", "Router HTTP(S) origin.", "URL"),
                        ("--auth-env", "Process variable containing scoped operator credential.", "NAME"),
                        ("--timeout", "Socket timeout, at most30 seconds.", "SECONDS"),
                        ("--filters", "JSON array of unique typed dimension/value pairs.", "JSON"),
                        ("--limit", "Bounded record/group limit.", "COUNT"),
                    )) + (() if action == "active" else tuple(_option(name, summary=help, value_name=value) for name, help, value in (
                        ("--granularity", "detail, daily or cumulative (default detail).", "CLASS"),
                        ("--from-utc", "Inclusive UTC Z start bound.", "TIME"),
                        ("--to-utc", "Exclusive UTC Z end bound.", "TIME"),
                        ("--group-by", "JSON array of unique dimensions.", "JSON"),
                        ("--cursor", "Opaque retained-query cursor.", "CURSOR"),
                        ("--require-complete", "Refuse incomplete coverage.", None),
                    ))),
                    docs_anchor="docs/cli/router.md#caller-accounting",
                ) for action, summary in (("active", "Read owned active samples."), ("recent", "Read last24h retained detail."), ("query", "Read exact supported retained history."))),
            ),
            _node(
                "workloads",
                "Read a bounded canonical workload snapshot from one router.",
                handler=_handler(
                    "anvil_serving.cli", attribute="_workload_command", argv_prefix=("router",)
                ),
                options=(
                    _option("--router-url", summary="Explicit loopback router /v1 URL (required).", value_name="URL"),
                    _option("--auth-env", summary="Environment variable containing the router credential (required).", value_name="NAME"),
                    _option("--expected-node", summary="Expected router node identity (required).", value_name="NODE"),
                    _option("--owner", summary="Filter by workload owner.", value_name="OWNER"),
                    _option("--kind", summary="Filter by workload kind.", value_name="KIND"),
                    _option("--state", summary="Filter by workload state.", value_name="STATE"),
                    _option("--host", summary="Filter records by observed host.", value_name="HOST"),
                    _option("--active-only", summary="Return only active workloads."),
                    _option("--recent-seconds", summary="Recent-workload window in seconds.", value_name="SECONDS"),
                    _option("--limit", summary="Maximum returned workloads.", value_name="COUNT"),
                ),
            ),
            _node(
                "diagnose",
                "Inspect active requests or retained request/session evidence without replaying it.",
                handler=_handler("anvil_serving.router_diagnostics", attribute="dispatch", argv_prefix=()),
                options=(
                    _option("--request-id", summary="Request identifier returned by the gateway.", value_name="ID"),
                    _option("--session-id", summary="Opaque session identifier to filter retained or active requests.", value_name="ID"),
                    _option("--active", summary="Read current active requests, optionally filtered by session."),
                    _option("--config", summary="Saved diagnostic connection settings; defaults to operator home.", value_name="PATH"),
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
                forward_resolution_options=True,
                options=CONFIRM_OPTIONS
                + (
                    _option("--config", summary="Complete router config to validate and install.", value_name="PATH"),
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
                    _option("--member", summary="Optional declared replica member; requires --tier.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "status"),),
                    allowed=("tier", "member", "router_url"),
                ),
            ),
            _resource_node(
                "quiesce",
                "Quiesce one router tier or declared member.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--member", summary="Optional declared replica member; requires --tier.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "quiesce"),),
                    allowed=("tier", "member", "router_url", "timeout", "dry_run"),
                ),
            ),
            _resource_node(
                "drain",
                "Wait for a quiesced tier or declared member to drain.",
                "anvil_serving.router_manage",
                role="router",
                options=(
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--member", summary="Optional declared replica member; requires --tier.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                    _option("--timeout", summary="Positive drain timeout.", value_name="SECONDS"),
                ),
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "drain"),),
                    allowed=("tier", "member", "router_url", "timeout", "dry_run"),
                ),
            ),
            _resource_node(
                "readmit",
                "Safely readmit one router tier or declared member.",
                "anvil_serving.router_manage",
                role="router",
                options=CONFIRM_OPTIONS
                + (
                    _option("--tier", summary="Tier id.", value_name="ID"),
                    _option("--member", summary="Optional declared replica member; requires --tier.", value_name="ID"),
                    _option("--router-url", summary="Private router base URL.", value_name="URL"),
                ),
                mutation="mutate",
                remote_operation=_remote(
                    "router_transition",
                    fixed=(("action", "readmit"),),
                    allowed=("tier", "member", "router_url", "timeout", "dry_run"),
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
