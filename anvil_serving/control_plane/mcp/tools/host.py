"""Explicit host MCP tool family."""

from __future__ import annotations


from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    capture as _capture,
)


def tool_doctor_summary(args: dict) -> dict:
    from .... import doctor

    no_config = _arg_bool(args.get("no_config"), False, name="no_config")
    config = None if no_config else args.get("config", doctor.DEFAULT_CONFIG)
    if config is not None and not isinstance(config, str):
        raise ToolError("bad_argument", "'config' must be a string")
    return _ok(doctor.checks_summary(config_path=config, config_explicit=bool(args.get("config"))))


def tool_host_summary(args: dict) -> dict:
    from .... import host

    if args:
        raise ToolError("bad_argument", "host_summary does not accept arguments")
    return _ok(host.host_summary())


def tool_gpu_inventory(args: dict) -> dict:
    from .... import gpus

    if args:
        raise ToolError("bad_argument", "gpu_inventory does not accept arguments")
    return _ok({"gpus": gpus.list_gpus()})


def tool_host_shared_memory(args: dict) -> dict:
    from .... import host

    if args:
        raise ToolError("bad_argument", "host_shared_memory does not accept arguments")
    return _ok(host.inspect_vllm_offload_shared_memory())


def tool_observability_collect(args: dict) -> dict:
    from ....observability.api import controller_collect

    capabilities = args.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ToolError("bad_argument", "capabilities must be a non-empty array")
    try:
        return _ok(controller_collect(capabilities))
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_argument", str(exc)) from exc


def tool_host_manage(args: dict) -> dict:
    from .... import host

    action = _str_arg(args, "action", required=True)
    if action not in {"wsl-config", "restart-docker", "reset-wsl", "reclaim-shared-memory"}:
        raise ToolError(
            "bad_action",
            "action must be wsl-config, restart-docker, reset-wsl, or reclaim-shared-memory",
        )
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    force = _arg_bool(args.get("force"), False, name="force")
    revert = _arg_bool(args.get("revert"), False, name="revert")
    memory = _bounded_int_arg(args, "memory", 0, min_value=0, max_value=4096)
    swap = _bounded_int_arg(args, "swap", 0, min_value=0, max_value=4096)
    target = {
        "action": action,
        "memory": memory or None,
        "swap": swap if "swap" in args else None,
        "revert": revert,
        "force": force,
    }
    if action != "wsl-config" and any(key in args for key in ("memory", "swap", "revert", "force")):
        raise ToolError("bad_argument", "memory, swap, revert, and force apply only to wsl-config")
    if action == "reclaim-shared-memory":
        inspection = host.inspect_vllm_offload_shared_memory()
        target["inspection"] = inspection
    if dry_run or not confirm:
        return _ok({"applied": False, "dry_run": True, "target": target})
    if action == "wsl-config":
        rc, stdout, stderr = _capture(
            lambda: host.cmd_wsl_config(
                memory_gb=memory or None,
                swap_gb=swap if "swap" in args else None,
                revert=revert,
                force=force,
            )
        )
    elif action == "restart-docker":
        rc, stdout, stderr = _capture(lambda: host.cmd_restart_docker(force=True))
    elif action == "reclaim-shared-memory":
        rc, stdout, stderr = _capture(
            lambda: host.cmd_shared_memory_reclaim(confirm=True)
        )
    else:
        rc, stdout, stderr = _capture(lambda: host.cmd_reset_wsl(force=True))
    result = {
        "applied": rc == 0,
        "dry_run": False,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "target": target,
    }
    if rc != 0:
        raise ToolError("command_failed", f"host {action} exited with status {rc}", result)
    return _ok(result)


FAMILY = ToolFamily(
    name="host",
    tools={
        "doctor_summary": {
            "description": "Run anvil-serving environment checks and return structured results.",
            "inputSchema": _schema(
                {
                    "config": {"type": "string"},
                    "no_config": {"type": "boolean"},
                }
            ),
            "handler": tool_doctor_summary,
        },
        "host_summary": {
            "description": "Return read-only WSL/Docker/GPU host checks; performs no repair or restart.",
            "inputSchema": _schema({}),
            "handler": tool_host_summary,
        },
        "gpu_inventory": {
            "description": "Return the local NVIDIA GPU inventory with stable UUIDs.",
            "inputSchema": _schema({}),
            "handler": tool_gpu_inventory,
        },
        "host_shared_memory": {
            "description": (
                "Inspect vLLM native KV-offload mmap ownership and reclaim eligibility "
                "without changing host state."
            ),
            "inputSchema": _schema({}),
            "handler": tool_host_shared_memory,
        },
        "observability_collect": {
            "description": "Collect bounded structured telemetry from declared local capabilities.",
            "inputSchema": _schema(
                {
                    "capabilities": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {"type": "string", "maxLength": 80},
                    },
                },
                required=["capabilities"],
            ),
            "handler": tool_observability_collect,
        },
        "host_manage": {
            "description": "Preview or run a bounded host repair operation on the controller host.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "memory": {"type": "integer", "minimum": 1, "maximum": 4096},
                    "swap": {"type": "integer", "minimum": 0, "maximum": 4096},
                    "revert": {"type": "boolean"},
                    "force": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                required=["action"],
            ),
            "handler": tool_host_manage,
        },
    },
)
