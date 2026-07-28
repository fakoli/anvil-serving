"""Explicit voice MCP tool family."""

from __future__ import annotations

import argparse
import os
import sys

from ..arguments import (
    arg_bool as _arg_bool,
    bounded_float_arg as _bounded_float_arg,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    capture as _capture,
)
from ....paths import resolve_topology_path


def _voice_cli_argv(
    action: str,
    config: str,
    *,
    topology: str,
    dry_run: bool = False,
    profile: str = "",
    topology_overlay: str = "",
    command_host: str = "",
    command_runtime: str = "",
    target: str = "",
    transport: str = "auto",
    experimental_model_workload: bool = False,
    ready_timeout: float = 3.0,
    tail: int = 200,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "voice",
        "audio",
        action,
        "--config",
        config,
    ]
    if profile:
        argv += ["--profile", profile]
    argv += ["--topology", topology]
    if topology_overlay:
        argv += ["--topology-overlay", topology_overlay]
    if command_host:
        argv += ["--command-host", command_host]
    if command_runtime:
        argv += ["--command-runtime", command_runtime]
    if target:
        argv += ["--target", target]
    if transport != "auto":
        argv += ["--transport", transport]
    if experimental_model_workload:
        argv.append("--experimental-model-workload")
    if dry_run:
        argv.append("--dry-run")
    if action == "status":
        argv += ["--ready-timeout", str(ready_timeout)]
    elif action == "logs":
        argv += ["--tail", str(tail)]
    return argv


def _voice_manage_plan(config: str, *, profile: str = "") -> dict:
    from ....voice import config as voice_config
    from ....voice.serves import native as native_serve

    try:
        raw = voice_config.load_raw_manifest(config)
        available_profiles = voice_config.profile_names(raw)
        data = (
            voice_config.load_manifest(config, profile=profile)
            if profile
            else voice_config.load_manifest(config)
        )
    except FileNotFoundError:
        raise ToolError("config_not_found", "voice manifest not found", {"config": config})
    except voice_config.ConfigError as exc:
        raise ToolError(
            "bad_config", "could not load voice manifest", {"config": config, "error": str(exc)}
        )
    voice = data.get("voice", {})
    audio = []
    for kind in ("stt", "tts"):
        table = voice.get(kind, {})
        lifecycle = table.get("lifecycle", "managed")
        item = {
            "kind": kind,
            "lifecycle": lifecycle,
            "base_url": table.get("base_url"),
            "model": table.get("model"),
        }
        if lifecycle == "native":
            cfg = native_serve.NativeServeConfig.from_table(kind, table)
            item.update(
                {
                    "start_command": native_serve.parse_command(cfg.start_command),
                    "stop_command": (
                        native_serve.parse_command(cfg.stop_command) if cfg.stop_command else None
                    ),
                    "workdir": cfg.workdir or None,
                    "pid_file": cfg.pid_file,
                    "log_file": cfg.log_file,
                    "ready_timeout": cfg.ready_timeout,
                    "stop_timeout": cfg.stop_timeout,
                }
            )
        elif lifecycle == "external":
            item["note"] = "external/manual lifecycle; voice_manage will skip it"
        else:
            item["note"] = "managed through the voice serve adapter and serves.toml"
        audio.append(item)
    return {
        "voice": voice.get("name", "anvil-voice"),
        "config": config,
        "profile": profile or None,
        "available_profiles": available_profiles,
        "audio_serves": audio,
    }


def tool_voice_manage(args: dict) -> dict:
    """Manage Dark-owned STT/TTS only after local topology authorization."""
    from ....topology import load_topology
    from ....voice import config as voice_config
    from ....voice import cli as voice_cli

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "status", "logs"}:
        raise ToolError(
            "bad_action",
            "action must be one of: up, down, status, logs",
            {"action": action},
        )
    config_arg = _str_arg(args, "config", "")
    config = voice_config.resolve_config_path(config_arg or None)
    profile = _str_arg(args, "profile", "")
    topology_overlay = _str_arg(args, "topology_overlay", "")
    command_host = _str_arg(args, "command_host", "")
    command_runtime = _str_arg(args, "command_runtime", "")
    target = _str_arg(args, "target", "")
    transport = _str_arg(args, "transport", "auto")
    if transport not in {"auto", "local", "controller"}:
        raise ToolError(
            "bad_transport",
            "transport must be one of: auto, local, controller",
            {"transport": transport},
        )
    experimental_model_workload = _arg_bool(
        args.get("experimental_model_workload"),
        False,
        name="experimental_model_workload",
    )
    topology_path = resolve_topology_path(
        _str_arg(args, "topology", "") or None,
        env_var="ANVIL_VOICE_TOPOLOGY",
    )
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)
    ready_timeout = _bounded_float_arg(args, "ready_timeout", 3.0, min_value=0.1, max_value=60.0)
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    plan = _voice_manage_plan(config, profile=profile)
    try:
        topology = load_topology(topology_path)
        owners = tuple(topology.resource_owner("%s-serve" % kind) for kind in ("stt", "tts"))
        if owners[0].host != owners[1].host or owners[0].runtime != owners[1].runtime:
            raise voice_config.ConfigError(
                "STT and TTS must be co-owned by one host/runtime for audio lifecycle"
            )
        cli_args = argparse.Namespace(
            config=config,
            profile=profile or None,
            topology=topology_path,
            topology_overlay=topology_overlay or None,
            command_host=command_host or None,
            command_runtime=command_runtime or None,
            target=target or None,
            transport=transport,
            experimental_model_workload=experimental_model_workload,
            ready_timeout=ready_timeout,
            tail=tail,
            operation_timeout=float(timeout_seconds),
        )
        data, targets, error, error_code = voice_cli._resolve_audio_operation(cli_args)
        if error:
            raise ToolError(
                "audio_target_refused",
                error,
                {"topology": topology_path, "exit_code": error_code},
            )
    except ToolError:
        raise
    except (OSError, ValueError) as exc:
        raise ToolError(
            "bad_audio_config",
            "could not resolve Dark audio ownership",
            {"config": config, "topology": topology_path, "error": str(exc)},
        )
    assert data is not None and targets is not None
    cli_args._resolved_audio = (data, targets)
    preview = action in {"up", "down"} and (dry_run or not confirm)
    argv = _voice_cli_argv(
        action,
        config,
        topology=topology_path,
        dry_run=preview,
        profile=profile,
        topology_overlay=topology_overlay,
        command_host=command_host,
        command_runtime=command_runtime,
        target=target,
        transport=transport,
        experimental_model_workload=experimental_model_workload,
        ready_timeout=ready_timeout,
        tail=tail,
    )
    target = {
        "action": action,
        "config": config,
        "profile": profile or None,
        "topology": topology_path,
        "owners": targets.as_dict(),
        "command_host": command_host or None,
        "command_runtime": command_runtime or None,
        "requested_target": target or None,
        "transport": transport,
        "topology_overlay": topology_overlay or None,
        "experimental_model_workload": experimental_model_workload,
        "timeout_seconds": timeout_seconds,
    }
    if action in {"status", "logs"}:
        handler = voice_cli.cmd_audio_status if action == "status" else voice_cli.cmd_audio_logs
        returncode, stdout, stderr = _capture(lambda: handler(cli_args))
        if returncode != 0:
            raise ToolError(
                "command_failed",
                "voice audio %s failed" % action,
                {"command": argv, "returncode": returncode, "stderr": stderr},
            )
        return _ok(
            {
                "applied": False,
                "target": target,
                "command": argv,
                "plan": plan,
                "output": stdout,
                "stderr": stderr,
            }
        )
    if preview:
        return _ok(
            {"applied": False, "dry_run": True, "target": target, "command": argv, "plan": plan}
        )
    result = voice_cli.execute_audio_lifecycle(
        data, action, targets=targets, timeout_seconds=float(timeout_seconds)
    )
    if result["returncode"] != 0:
        raise ToolError(
            "command_failed",
            "voice audio lifecycle failed",
            {"command": argv, "lifecycle": result},
        )
    applied = any(item.get("lifecycle") != "external" for item in plan.get("audio_serves", []))
    return _ok(
        {
            "applied": applied,
            "dry_run": False,
            "target": target,
            "command": argv,
            "plan": plan,
            "lifecycle": result,
        }
    )


def tool_voice_proxy_manage(args: dict) -> dict:
    """Manage the persistent Mini proxy process without touching model serves."""
    from ....topology import load_topology
    from ....voice import config as voice_config
    from ....voice.realtime_service import ProxyProcessConfig, RealtimeProxyProcessService

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "restart", "status", "logs"}:
        raise ToolError(
            "bad_action",
            "action must be one of: up, down, restart, status, logs",
            {"action": action},
        )
    config = voice_config.resolve_config_path(_str_arg(args, "config", "") or None)
    profile = _str_arg(args, "profile", "")
    topology_path = resolve_topology_path(
        _str_arg(args, "topology", "") or None,
        env_var="ANVIL_VOICE_TOPOLOGY",
    )
    try:
        data = voice_config.load_manifest(config, profile=profile or None)
        topology = load_topology(topology_path)
        targets = voice_config.resolve_proxy_targets(
            topology,
            operation="voice-proxy-%s" % action,
            transport="local",
        )
    except (OSError, ValueError) as exc:
        raise ToolError(
            "bad_proxy_config",
            "could not resolve Mini proxy configuration",
            {"config": config, "topology": topology_path, "error": str(exc)},
        )
    voice = data["voice"]
    process = RealtimeProxyProcessService(
        ProxyProcessConfig(
            config_path=config,
            topology_path=topology_path,
            profile=profile or None,
            host=voice.get("realtime_host", "127.0.0.1"),
            port=int(voice.get("realtime_port", 8765)),
            owner=targets.proxy.resource_host.id,
            pid_file=_str_arg(args, "pid_file", "")
            or os.path.join("~/.anvil-serving/run", "voice-proxy.pid"),
            log_file=_str_arg(args, "log_file", "")
            or os.path.join("~/.anvil-serving/run", "voice-proxy.log"),
            ready_timeout=float(
                _bounded_int_arg(args, "timeout_seconds", 15, min_value=1, max_value=300)
            ),
        )
    )
    if action == "status":
        return _ok(process.status())
    if action == "logs":
        tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
        return _ok(process.logs(tail=tail))
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    preview = dry_run or not confirm
    result = getattr(process, action)(dry_run=preview)
    result["dry_run"] = preview
    if preview:
        result["applied"] = False
    elif action == "restart":
        result["applied"] = any(
            isinstance(step, dict) and bool(step.get("applied"))
            for step in (result.get("down"), result.get("up"))
        )
    else:
        result["applied"] = bool(result.get("applied", result.get("returncode") == 0))
    return _ok(result)


FAMILY = ToolFamily(
    name="voice",
    tools={
        "voice_manage": {
            "description": "Preview or run guarded voice STT/TTS lifecycle actions with optional voice profile selection.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string", "enum": ["up", "down", "status", "logs"]},
                    "config": {"type": "string"},
                    "profile": {"type": "string"},
                    "topology": {"type": "string"},
                    "topology_overlay": {"type": "string"},
                    "command_host": {"type": "string"},
                    "command_runtime": {"type": "string"},
                    "target": {"type": "string"},
                    "transport": {
                        "type": "string",
                        "enum": ["auto", "local", "controller"],
                        "default": "auto",
                    },
                    "experimental_model_workload": {"type": "boolean"},
                    "ready_timeout": {
                        "type": "number",
                        "minimum": 0.1,
                        "maximum": 60.0,
                        "default": 3.0,
                    },
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
                },
                required=["action"],
            ),
            "handler": tool_voice_manage,
        },
        "voice_proxy_manage": {
            "description": "Manage the persistent Mini-owned Realtime proxy process.",
            "inputSchema": _schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["up", "down", "restart", "status", "logs"],
                    },
                    "config": {"type": "string"},
                    "profile": {"type": "string"},
                    "topology": {"type": "string"},
                    "pid_file": {"type": "string"},
                    "log_file": {"type": "string"},
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 300, 15),
                },
                required=["action"],
            ),
            "handler": tool_voice_proxy_manage,
        },
    },
)
