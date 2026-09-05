"""Command declarations for portable host-supervised services."""

from .common import CONFIRM_OPTIONS
from .spec import CommandNode, _node, _option, _remote, _resource_node


_RESOLUTION_OPTIONS = (
    _option("--manifest", summary="Local services.toml override; not accepted by remote controllers.", value_name="PATH"),
    _option("--tail", summary="Maximum bounded log lines.", value_name="LINES"),
    _option("--timeout-seconds", summary="Bounded operation timeout.", value_name="SECONDS"),
)

# Service mutations preview by default in their local handler.  The generic
# confirmation policy treats this conditional option as the apply gate.
_PREVIEW_APPLY_OPTION = (
    _option(
        "--no-dry-run",
        summary="Apply instead of the default preview; requires --confirm.",
        requires_confirmation=True,
    ),
)

_ADOPT_OPTIONS = (
    _option("--manager", summary="Declared supervisor manager.", value_name="MANAGER"),
    _option("--service-label", summary="Declared launchd service label.", value_name="LABEL"),
    _option("--resource", summary="Topology resource identity.", value_name="RESOURCE"),
    _option("--engine", summary="Declared engine adapter.", value_name="ENGINE"),
    _option("--support", summary="Binding support classification.", value_name="CLASS"),
    _option("--container", summary="Declared Docker container identity.", value_name="CONTAINER"),
    _option("--endpoint", summary="Declared loopback endpoint.", value_name="URL"),
    _option("--model", summary="Declared served model name.", value_name="MODEL"),
    _option("--health-path", summary="Declared health path.", value_name="PATH"),
    _option("--models-path", summary="Declared models inventory path.", value_name="PATH"),
    _option("--feature", summary="Declared feature kind.", value_name="FEATURE"),
    _option("--startup-policy", summary="Declared automatic-start policy.", value_name="POLICY"),
    _option("--memory-mib", summary="Declared service memory budget.", value_name="MIB"),
    _option("--serve", summary="Owning model serve for reservation admission.", value_name="SERVE"),
    _option("--serve-manifest", summary="Local owning serves manifest override.", value_name="PATH"),
)


def _service_node(
    name: str,
    summary: str,
    tool: str,
    *,
    action: str | None = None,
    mutation: bool = False,
    options=(),
) -> CommandNode:
    context_arguments = (
        "service",
        "dry_run",
        "confirm",
        "tail",
        "timeout_seconds",
    )
    adoption_arguments = (
        "manager",
        "service_label",
        "resource",
        "engine",
        "support",
        "container",
        "endpoint",
        "model",
        "health_path",
        "models_path",
        "feature",
        "startup_policy",
        "memory_mib",
        "serve",
    )
    remote = _remote(
        tool,
        fixed=(() if action is None else (("action", action),)),
        confirmed=(() if not mutation else (("confirm", True),)),
        allowed=context_arguments + (adoption_arguments if tool == "host_services_manage" else ()),
        positionals=("service",),
    )
    return _resource_node(
        name,
        summary,
        "anvil_serving.service_runtime.cli",
        role="host",
        mutation="mutate" if mutation else "read",
        options=options,
        argv_prefix=((action or name),),
        forward_resolution_options=True,
        forward_confirm_flag=mutation,
        handler_attribute="run",
        remote_operation=remote,
        execution_runtime_roles=("native", "docker", "wsl"),
        docs_anchor="docs/cli/host.md#command-map",
    )


def services() -> CommandNode:
    """Return the resource-owner service lifecycle child of ``host``."""
    return _node(
        "services",
        "Inspect, adopt, and operate declared portable supervised services.",
        children=(
            _service_node("status", "Show bounded declared-service status.", "host_services_status", options=_RESOLUTION_OPTIONS),
            _service_node("discover", "Discover eligible unmanaged local services.", "host_services_discover"),
            _service_node(
                "capabilities",
                "Show the owning runtime's supported service operations.",
                "host_services_capabilities",
            ),
            _service_node(
                "logs",
                "Read a bounded declared-service log tail.",
                "host_services_logs",
                options=_RESOLUTION_OPTIONS,
            ),
            _service_node(
                "adopt",
                "Preview or adopt an exact existing launchd or Docker service.",
                "host_services_manage",
                action="adopt",
                mutation=True,
                options=CONFIRM_OPTIONS + _PREVIEW_APPLY_OPTION + _RESOLUTION_OPTIONS + _ADOPT_OPTIONS,
            ),
            *(
                _service_node(
                    action,
                    summary,
                    "host_services_manage",
                    action=action,
                    mutation=True,
                    options=CONFIRM_OPTIONS + _PREVIEW_APPLY_OPTION + _RESOLUTION_OPTIONS,
                )
                for action, summary in (
                    ("install", "Render a declared service supervisor definition without starting it."),
                    ("up", "Start one declared service."),
                    ("down", "Stop one declared service."),
                    ("restart", "Restart one declared service."),
                    ("enable", "Enable automatic start for one declared service."),
                    ("disable", "Disable automatic start for one declared service."),
                )
            ),
        ),
        docs_anchor="docs/cli/host.md#command-map",
    )
