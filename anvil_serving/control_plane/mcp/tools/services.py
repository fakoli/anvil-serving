"""Typed MCP operations for topology-owned supervised services."""

from __future__ import annotations

import os
from pathlib import Path

from ..arguments import arg_bool as _arg_bool
from ..arguments import bounded_int_arg as _bounded_int_arg
from ..arguments import schema as _schema
from ..arguments import str_arg as _str_arg
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..security import RAW_COMMAND_KEYS, RAW_SECRET_KEYS
from ....service_runtime.contracts import MUTATING_ACTIONS, ServiceError
from ....paths import config_path
from ....service_runtime.operations import execute


_MANAGE_ACTIONS = frozenset(MUTATING_ACTIONS)
_RAW_FIELDS = RAW_COMMAND_KEYS | RAW_SECRET_KEYS | {"env", "environment", "environ"}


def _reject_private_inputs(args: dict) -> None:
    fields = sorted(
        str(key) for key in args
        if str(key).lower().replace("-", "_") in _RAW_FIELDS
    )
    if fields:
        raise ToolError("bad_argument", "raw command and secret inputs are not accepted", {"fields": fields})


def _binding(args: dict, action: str, service: str) -> dict | None:
    if action != "adopt":
        return None
    manager = _str_arg(args, "manager", required=True)
    resource = _str_arg(args, "resource", required=True)
    engine = _str_arg(args, "engine", required=True)
    if manager not in {"launchd", "docker"}:
        raise ToolError("bad_argument", "manager must be launchd or docker")
    support = _str_arg(args, "support", "supported")
    if support not in {"supported", "legacy"}:
        raise ToolError("bad_argument", "support must be supported or legacy")
    binding = {
        "id": service,
        "resource": resource,
        "manager": manager,
        "engine": engine,
        "support": support,
    }
    if manager == "launchd":
        if not hasattr(os, "getuid"):
            raise ToolError("unsupported_platform", "launchd requires the owning macOS host")
        label = _str_arg(args, "service_label", required=True)
        binding.update(
            {
                "label": label,
                "owner_uid": os.getuid(),
                "definition": str(Path.home() / "Library" / "LaunchAgents" / (label + ".plist")),
            }
        )
    else:
        binding["container"] = _str_arg(args, "container", required=True)
    for name in ("endpoint", "model", "health_path", "models_path", "feature", "startup_policy"):
        value = _str_arg(args, name, "")
        if name == "startup_policy" and value not in {"", "always", "unless-stopped"}:
            raise ToolError("bad_argument", "startup_policy must be always or unless-stopped")
        if value:
            binding[name] = value
    if "memory_mib" in args:
        binding["memory_mib"] = _bounded_int_arg(
            args, "memory_mib", 1, min_value=1, max_value=1048576
        )
    serve = _str_arg(args, "serve", "")
    if serve:
        binding["serve"] = serve
        binding["serve_manifest"] = config_path("serves.toml")
    return binding


def _execute(args: dict, action: str, *, service_required: bool = False) -> dict:
    _reject_private_inputs(args)
    service = _str_arg(args, "service", required=service_required)
    topology = _str_arg(args, "topology", "")
    topology_overlay = _str_arg(args, "topology_overlay", "")
    command_host = _str_arg(args, "command_host", "")
    command_runtime = _str_arg(args, "command_runtime", "")
    target = _str_arg(args, "target", "")
    transport = _str_arg(args, "transport", "local")
    if transport not in {"auto", "local", "controller", "ssh"}:
        raise ToolError("bad_argument", "transport must be auto, local, controller, or ssh")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    tail = _bounded_int_arg(args, "tail", 100, min_value=1, max_value=1000)
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 30, min_value=1, max_value=7200)
    try:
        return execute(
            action,
            service or None,
            manifest=None,
            topology=topology or None,
            topology_overlay=topology_overlay or None,
            command_host=command_host or None,
            command_runtime=command_runtime or None,
            target=target or None,
            transport=transport,
            dry_run=dry_run,
            confirm=confirm,
            tail=tail,
            timeout_seconds=timeout_seconds,
            binding=_binding(args, action, service) if action == "adopt" else None,
            remote=True,
        )
    except ServiceError as exc:
        raise ToolError(exc.code, str(exc), getattr(exc, "details", None)) from None


def tool_host_services_status(args: dict) -> dict:
    return _ok(_execute(args, "status"))


def tool_host_services_discover(args: dict) -> dict:
    return _ok(_execute(args, "discover"))


def tool_host_services_capabilities(args: dict) -> dict:
    return _ok(_execute(args, "capabilities"))


def tool_host_services_logs(args: dict) -> dict:
    return _ok(_execute(args, "logs", service_required=True))


def tool_host_services_manage(args: dict) -> dict:
    action = _str_arg(args, "action", required=True)
    if action not in _MANAGE_ACTIONS:
        raise ToolError(
            "bad_action",
            "action must be one of: " + ", ".join(sorted(_MANAGE_ACTIONS)),
            {"action": action},
        )
    return _ok(_execute(args, action, service_required=True))


_CONTEXT_PROPERTIES = {
    "service": {"type": "string", "minLength": 1, "maxLength": 128},
    "dry_run": {"type": "boolean"},
    "confirm": {"type": "boolean"},
    "tail": {"type": "integer", "minimum": 1, "maximum": 1000},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 7200},
}

_ADOPT_PROPERTIES = {
    "manager": {"type": "string", "enum": ["launchd", "docker"]},
    "service_label": {"type": "string", "minLength": 1, "maxLength": 128},
    "resource": {"type": "string", "minLength": 1, "maxLength": 128},
    "engine": {"type": "string", "minLength": 1, "maxLength": 128},
    "support": {"type": "string", "enum": ["supported", "legacy"]},
    "container": {"type": "string", "minLength": 1, "maxLength": 128},
    "endpoint": {"type": "string", "minLength": 1, "maxLength": 2048},
    "model": {"type": "string", "minLength": 1, "maxLength": 2048},
    "health_path": {"type": "string", "minLength": 1, "maxLength": 2048},
    "models_path": {"type": "string", "minLength": 1, "maxLength": 2048},
    "feature": {"type": "string", "minLength": 1, "maxLength": 2048},
    "startup_policy": {"type": "string", "enum": ["always", "unless-stopped"]},
    "memory_mib": {"type": "integer", "minimum": 1, "maximum": 1048576},
    "serve": {"type": "string", "minLength": 1, "maxLength": 128},
}


FAMILY = ToolFamily(
    name="services",
    tools={
        "host_services_status": {
            "description": "Inspect bounded declared service supervisor, engine, and readiness state.",
            "inputSchema": _schema(_CONTEXT_PROPERTIES),
            "handler": tool_host_services_status,
        },
        "host_services_discover": {
            "description": "Discover bounded eligible local supervisor services without adopting them.",
            "inputSchema": _schema(_CONTEXT_PROPERTIES),
            "handler": tool_host_services_discover,
        },
        "host_services_capabilities": {
            "description": "Return the owning host service-runtime capabilities and supported actions.",
            "inputSchema": _schema(_CONTEXT_PROPERTIES),
            "handler": tool_host_services_capabilities,
        },
        "host_services_logs": {
            "description": "Read a bounded tail from one declared service's safe logs.",
            "inputSchema": _schema(_CONTEXT_PROPERTIES, required=["service"]),
            "handler": tool_host_services_logs,
        },
        "host_services_manage": {
            "description": "Preview or run one confirmed declared-service lifecycle action on its owner.",
            "inputSchema": _schema(
                {"action": {"type": "string", "enum": sorted(_MANAGE_ACTIONS)}}
                | _CONTEXT_PROPERTIES
                | _ADOPT_PROPERTIES,
                required=["action", "service"],
            ),
            "handler": tool_host_services_manage,
        },
    },
)
