"""Explicit openclaw MCP tool family."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

from ....envfile import resolve_env_value
from ..arguments import (
    arg_bool as _arg_bool,
    bounded_float_arg as _bounded_float_arg,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    probe_api_key_env as _probe_api_key_env,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    capture as _capture,
)
from ..security import (
    safe_probe_url as _safe_probe_url,
)


def tool_openclaw_sync(args: dict) -> dict:
    from .... import harness

    config = _str_arg(args, "config", required=True)
    base_url = _safe_probe_url(_str_arg(args, "base_url", "http://127.0.0.1:8000/v1"))
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = _probe_api_key_env(
        {"api_key_env": _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")}
    )
    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    gateway_path = _str_arg(args, "gateway_path", "~/.openclaw/openclaw.json")
    out = _str_arg(args, "out", "")
    voice = _arg_bool(args.get("voice"), False, name="voice")
    voice_realtime_url = _str_arg(
        args,
        "voice_realtime_url",
        harness.DEFAULT_ANVIL_VOICE_REALTIME_URL,
    )
    voice_model = _str_arg(args, "voice_model", "")
    voice_consult_model = _str_arg(args, "voice_consult_model", "")
    voice_consult_thinking_level = _str_arg(args, "voice_consult_thinking_level", "off")
    voice_consult_bootstrap_context_mode = _str_arg(
        args,
        "voice_consult_bootstrap_context_mode",
        "lightweight",
    )
    try:
        voice_consult_thinking_level = (
            harness._normalize_voice_consult_thinking_level(voice_consult_thinking_level) or ""
        )
        voice_consult_bootstrap_context_mode = (
            harness._normalize_voice_consult_bootstrap_context_mode(
                voice_consult_bootstrap_context_mode
            )
            or ""
        )
    except ValueError as exc:
        raise ToolError(
            "bad_argument",
            str(exc),
            {
                "voice_consult_thinking_level": voice_consult_thinking_level,
                "voice_consult_bootstrap_context_mode": voice_consult_bootstrap_context_mode,
            },
        )
    if "voice_api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw voice_api_key is not accepted; set voice_api_key_env to the credential env var name",
        )
    voice_api_key_env = _str_arg(args, "voice_api_key_env", "")
    if voice_api_key_env:
        try:
            harness._validate_env_var_name(voice_api_key_env, arg_name="voice_api_key_env")
        except ValueError as exc:
            raise ToolError(
                "bad_voice_api_key_env", str(exc), {"voice_api_key_env": voice_api_key_env}
            )
    overwrite = _arg_bool(args.get("overwrite"), False, name="overwrite")
    restart = _arg_bool(args.get("restart"), False, name="restart")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    if gateway_host:
        try:
            harness._validate_gateway_target(gateway_host, gateway_user)
        except ValueError as exc:
            raise ToolError(
                "bad_gateway_target",
                str(exc),
                {"gateway_host": gateway_host, "gateway_user": gateway_user},
            )

    try:
        preview = harness.openclaw_sync_preview(
            config,
            base_url=base_url,
            api_key_env=api_key_env,
            voice=voice,
            voice_realtime_url=voice_realtime_url,
            voice_model=voice_model or None,
            voice_consult_model=voice_consult_model,
            voice_consult_thinking_level=voice_consult_thinking_level,
            voice_consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            voice_api_key_env=voice_api_key_env or None,
        )
    except FileNotFoundError:
        raise ToolError("config_not_found", "router config not found", {"config": config})
    except Exception as exc:
        raise ToolError(
            "bad_config", "could not render OpenClaw config", {"config": config, "error": str(exc)}
        )

    stdout_only = out == "-"
    target = {
        "gateway_host": gateway_host or None,
        "gateway_user": gateway_user or None,
        "gateway_path": gateway_path,
        "out": out or None,
        "voice": voice,
        "voice_realtime_url": voice_realtime_url if voice else None,
        "voice_model": preview.get("voice_model") if voice else None,
        "voice_consult_model": voice_consult_model if voice and voice_consult_model else None,
        "voice_consult_thinking_level": voice_consult_thinking_level if voice else None,
        "voice_consult_bootstrap_context_mode": (
            voice_consult_bootstrap_context_mode if voice else None
        ),
        "voice_api_key_env": voice_api_key_env or None,
        "overwrite": overwrite,
        "restart": restart,
        "timeout_seconds": timeout_seconds,
    }
    if dry_run or not confirm:
        return _ok({"applied": False, "target": target, "preview": preview})
    if not gateway_host and (not out or stdout_only):
        raise ToolError(
            "missing_target",
            "openclaw sync apply requires gateway_host or a real out path; '-' is render-only",
            {"target": target},
        )
    applied_payload = {}
    rc, stdout, stderr = _capture(
        lambda: harness.cmd_sync_openclaw(
            config,
            out=out or None,
            base_url=base_url,
            api_key_env=api_key_env,
            voice=voice,
            voice_realtime_url=voice_realtime_url,
            voice_model=voice_model or None,
            voice_consult_model=voice_consult_model,
            voice_consult_thinking_level=voice_consult_thinking_level,
            voice_consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            voice_api_key_env=voice_api_key_env or None,
            gateway_host=gateway_host or None,
            gateway_user=gateway_user or None,
            gateway_path=gateway_path,
            overwrite=overwrite,
            restart=restart,
            timeout_seconds=timeout_seconds,
            _replace_provider_keys=tuple(
                key
                for arg_name, key in (
                    ("base_url", "baseUrl"),
                    ("api_key_env", "apiKey"),
                )
                if arg_name in args
            ),
            _applied_payload=applied_payload,
        )
    )
    if rc == 0 and applied_payload:
        preview = harness._openclaw_payload_summary(
            applied_payload,
            voice=voice,
        )
    result = {
        "applied": rc == 0,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "target": target,
        "preview": {
            "model_count": preview["model_count"],
            "model_ids": preview["model_ids"],
            "base_url": preview["base_url"],
            "api_key": preview["api_key"],
            "direct_aliases": preview["direct_aliases"],
            "image_model": preview["image_model"],
            "voice": preview["voice"],
            "voice_provider": preview["voice_provider"],
            "voice_realtime_url": preview["voice_realtime_url"],
            "voice_model": preview["voice_model"],
            "voice_consult_model": preview["voice_consult_model"],
            "voice_consult_thinking_level": preview["voice_consult_thinking_level"],
            "voice_consult_bootstrap_context_mode": preview["voice_consult_bootstrap_context_mode"],
        },
    }
    if rc != 0:
        raise ToolError("command_failed", "openclaw sync exited with status %s" % rc, result)
    return _ok(result)


def tool_client_catalog_sync(args: dict) -> dict:
    """Preview or apply the fail-closed OpenClaw/Hermes/Pi capability reconciler."""
    from .... import harness
    from ....client_catalog_sync import ClientCatalogError, sync_clients

    base_url = _safe_probe_url(
        _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    )
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; use api_key_env",
        )
    api_key_env = _probe_api_key_env(
        {"api_key_env": _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")}
    )
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    restart_on_change = _arg_bool(
        args.get("restart_openclaw_on_change"),
        False,
        name="restart_openclaw_on_change",
    )
    restart_hermes_on_change = _arg_bool(
        args.get("restart_hermes_on_change"),
        False,
        name="restart_hermes_on_change",
    )
    hermes_bin = _str_arg(args, "hermes_bin", "~/.local/bin/hermes")
    timeout_seconds = _bounded_int_arg(
        args, "timeout_seconds", 15, min_value=1, max_value=300
    )
    try:
        result = sync_clients(
            base_url=base_url,
            api_key_env=api_key_env,
            clients=_str_arg(args, "clients", "openclaw,pi"),
            openclaw_config=_str_arg(
                args, "openclaw_config", "~/.openclaw/openclaw.json"
            ),
            hermes_config=_str_arg(
                args, "hermes_config", "~/.hermes/config.yaml"
            ),
            hermes_bin=hermes_bin,
            hermes_home=_str_arg(args, "hermes_home", "~/.hermes"),
            hermes_profiles=_str_arg(args, "hermes_profiles", "") or None,
            pi_models=_str_arg(args, "pi_models", "~/.pi/agent/models.json"),
            pi_settings=_str_arg(args, "pi_settings", "~/.pi/agent/settings.json"),
            state_path=_str_arg(
                args, "state_path", "~/.anvil-serving/state/client-catalog.json"
            ),
            backup_root=_str_arg(
                args,
                "backup_root",
                "~/.anvil-serving/backups/client-catalog",
            ),
            restart_openclaw_on_change=restart_on_change,
            restart_hermes_on_change=restart_hermes_on_change,
            dry_run=dry_run,
            confirm=confirm,
            timeout_seconds=timeout_seconds,
            restart=lambda: harness.cmd_restart_openclaw(
                timeout_seconds=harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS
            ),
            restart_hermes=lambda: harness._restart_hermes_default(
                hermes_bin=hermes_bin,
                timeout_seconds=harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
            ),
        )
    except (OSError, ClientCatalogError) as exc:
        raise ToolError("client_catalog_sync_failed", str(exc)) from exc
    return _ok(result)


def _routed_eval_output_path(output: str, run_id: str) -> str:
    root = Path(os.path.expanduser("~/.anvil-serving/evidence/routed-eval")).resolve()
    selected = Path(
        os.path.expanduser(output) if output else root / (run_id + ".json")
    ).resolve()
    try:
        selected.relative_to(root)
    except ValueError:
        raise ToolError(
            "unsafe_output_path",
            "remote routed-eval output must remain under the private evidence root",
        ) from None
    return str(selected)


def tool_routed_eval(args: dict) -> dict:
    """Run fail-closed router and real-client acceptance on the owning host."""
    from ....routed_eval import run_routed_eval

    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; use api_key_env",
        )
    api_key_env = _probe_api_key_env(
        {"api_key_env": _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")}
    )
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    if not dry_run and not confirm:
        raise ToolError(
            "human_approval_required",
            "live routed evaluation requires confirm=true",
        )
    runtime_environment: dict[str, str] = {}
    if not dry_run:
        token, _source = resolve_env_value(api_key_env)
        if token:
            runtime_environment[api_key_env] = token
    run_id = _str_arg(args, "run_id", "") or ("routed-" + uuid.uuid4().hex)
    output = _routed_eval_output_path(_str_arg(args, "output", ""), run_id)
    timeout_seconds = _bounded_float_arg(
        args,
        "timeout_seconds",
        600.0,
        min_value=1.0,
        max_value=3600.0,
    )
    try:
        artifact = run_routed_eval(
            base_url=base_url,
            alias=_str_arg(args, "model", required=True),
            api_key_env=api_key_env,
            expected_served_model=_str_arg(
                args, "expected_served_model", required=True
            ),
            expected_config_fingerprint=(
                _str_arg(args, "expected_config_fingerprint", "") or None
            ),
            expected_router_config_sha256=(
                _str_arg(args, "expected_router_config_sha256", "") or None
            ),
            min_context_tokens=_bounded_int_arg(
                args,
                "min_context_tokens",
                1,
                min_value=1,
                max_value=10_000_000,
            ),
            clients=_str_arg(args, "clients", "openclaw,hermes"),
            openclaw_provider=_str_arg(args, "openclaw_provider", "anvil"),
            hermes_provider=_str_arg(args, "hermes_provider", "anvil"),
            hermes_expected_provider=_str_arg(
                args, "hermes_expected_provider", "custom"
            ),
            timeout_seconds=timeout_seconds,
            output=output,
            run_id=run_id,
            dry_run=dry_run,
            sync_harnesses=not _arg_bool(
                args.get("no_harness_sync"), False, name="no_harness_sync"
            ),
            openclaw_config=os.path.expanduser(
                _str_arg(args, "openclaw_config", "~/.openclaw/openclaw.json")
            ),
            pi_models=os.path.expanduser(
                _str_arg(args, "pi_models", "~/.pi/agent/models.json")
            ),
            pi_settings=os.path.expanduser(
                _str_arg(args, "pi_settings", "~/.pi/agent/settings.json")
            ),
            client_state_path=os.path.expanduser(
                _str_arg(
                    args,
                    "client_state_path",
                    "~/.anvil-serving/state/client-catalog.json",
                )
            ),
            client_backup_root=os.path.expanduser(
                _str_arg(
                    args,
                    "client_backup_root",
                    "~/.anvil-serving/backups/client-catalog",
                )
            ),
            environment=runtime_environment,
        )
    except (OSError, ValueError) as exc:
        raise ToolError("routed_eval_failed", str(exc)) from exc
    if not dry_run and artifact.get("passed") is not True:
        raise ToolError(
            "routed_eval_failed",
            "router or real-client acceptance failed",
            artifact,
        )
    return _ok(artifact)


def tool_hermes_media_sync(args: dict) -> dict:
    """Preview or apply the narrow Hermes media MCP and skill reconciler."""

    from .... import harness
    from ....client_catalog_sync import ClientCatalogError, sync_hermes_media

    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    restart_on_change = _arg_bool(
        args.get("restart_hermes_on_change"),
        False,
        name="restart_hermes_on_change",
    )
    hermes_bin = _str_arg(args, "hermes_bin", "~/.local/bin/hermes")
    timeout_seconds = _bounded_int_arg(
        args,
        "timeout_seconds",
        15,
        min_value=1,
        max_value=120,
    )
    try:
        result = sync_hermes_media(
            hermes_bin=hermes_bin,
            hermes_home=_str_arg(args, "hermes_home", "~/.hermes"),
            hermes_profiles=_str_arg(args, "hermes_profiles", "default"),
            skill_path=_str_arg(
                args,
                "skill_path",
                "~/.hermes/skills/anvil-media/SKILL.md",
            ),
            backup_root=_str_arg(
                args,
                "backup_root",
                "~/.anvil-serving/backups/hermes-media",
            ),
            anvil_command=_str_arg(args, "anvil_command", "anvil-serving"),
            mcp_url_env=_str_arg(args, "mcp_url_env", "ANVIL_MEDIA_MCP_URL"),
            token_env=_str_arg(args, "token_env", "ANVIL_ROUTER_TOKEN"),
            restart_hermes_on_change=restart_on_change,
            dry_run=dry_run,
            confirm=confirm,
            timeout_seconds=timeout_seconds,
            restart_hermes=lambda: harness._restart_hermes_default(
                hermes_bin=hermes_bin,
                timeout_seconds=harness.DEFAULT_TRANSPORT_TIMEOUT_SECONDS,
            ),
        )
    except (OSError, ClientCatalogError) as exc:
        raise ToolError("hermes_media_sync_failed", str(exc)) from exc
    return _ok(result)


def tool_openclaw_gateway_restart(args: dict) -> dict:
    from .... import harness

    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    argv = ["openclaw", "gateway", "restart"]
    if gateway_host:
        try:
            harness._validate_gateway_target(gateway_host, gateway_user)
        except ValueError as exc:
            raise ToolError(
                "bad_gateway_target",
                str(exc),
                {"gateway_host": gateway_host, "gateway_user": gateway_user},
            )
        target = ("%s@%s" % (gateway_user, gateway_host)) if gateway_user else gateway_host
        argv = [
            "ssh",
            *harness._ssh_options(timeout_seconds),
            "--",
            target,
            harness._REMOTE_RESTART_COMMAND,
        ]
    if dry_run or not confirm:
        return _ok({"restarted": False, "dry_run": True, "command": argv})
    rc, stdout, stderr = _capture(
        lambda: harness.cmd_restart_openclaw(
            gateway_host=gateway_host or None,
            gateway_user=gateway_user or None,
            timeout_seconds=timeout_seconds,
        )
    )
    result = {"restarted": rc == 0, "returncode": rc, "stdout": stdout, "stderr": stderr}
    if rc != 0:
        raise ToolError("command_failed", "openclaw restart exited with status %s" % rc, result)
    return _ok(result)


def tool_openclaw_gateway_status(args: dict) -> dict:
    from .... import harness

    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    max_output_bytes = _bounded_int_arg(
        args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
    )
    try:
        result = harness.openclaw_gateway_status(
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    except ValueError as exc:
        raise ToolError("bad_argument", str(exc))
    if not result.get("ok"):
        raise ToolError("command_failed", "OpenClaw gateway status failed", result)
    return _ok(result)


FAMILY = ToolFamily(
    name="openclaw",
    tools={
        "openclaw_sync": {
            "description": "Preview or apply an OpenClaw provider config from the router's direct model aliases.",
            "inputSchema": _schema(
                {
                    "config": {"type": "string"},
                    "base_url": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "gateway_host": {"type": "string"},
                    "gateway_user": {"type": "string"},
                    "gateway_path": {"type": "string"},
                    "out": {"type": "string"},
                    "voice": {"type": "boolean"},
                    "voice_realtime_url": {"type": "string"},
                    "voice_model": {"type": "string"},
                    "voice_consult_model": {"type": "string"},
                    "voice_consult_thinking_level": {
                        "type": "string",
                        "enum": [
                            "adaptive",
                            "high",
                            "low",
                            "max",
                            "medium",
                            "minimal",
                            "off",
                            "xhigh",
                        ],
                    },
                    "voice_consult_bootstrap_context_mode": {
                        "type": "string",
                        "enum": ["full", "lightweight"],
                    },
                    "voice_api_key_env": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "restart": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
                },
                required=["config"],
            ),
            "handler": tool_openclaw_sync,
        },
        "openclaw_gateway_restart": {
            "description": "Restart the OpenClaw gateway locally or over SSH. Requires confirm=true.",
            "inputSchema": _schema(
                {
                    "gateway_host": {"type": "string"},
                    "gateway_user": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
                }
            ),
            "handler": tool_openclaw_gateway_restart,
        },
        "openclaw_gateway_status": {
            "description": "Read bounded local OpenClaw gateway status.",
            "inputSchema": _schema(
                {
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
                    "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
                }
            ),
            "handler": tool_openclaw_gateway_status,
        },
        "client_catalog_sync": {
            "description": (
                "Reconcile local OpenClaw, Hermes profiles, and Pi model limits from authenticated "
                "router status and capability metadata. Requires confirm=true to write."
            ),
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "clients": {"type": "string"},
                    "openclaw_config": {"type": "string"},
                    "hermes_config": {"type": "string"},
                    "hermes_bin": {"type": "string"},
                    "hermes_home": {"type": "string"},
                    "hermes_profiles": {"type": "string"},
                    "pi_models": {"type": "string"},
                    "pi_settings": {"type": "string"},
                    "state_path": {"type": "string"},
                    "backup_root": {"type": "string"},
                    "restart_openclaw_on_change": {"type": "boolean"},
                    "restart_hermes_on_change": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 300, 15),
                }
            ),
            "handler": tool_client_catalog_sync,
        },
        "routed_eval": {
            "description": (
                "Run fail-closed router identity, client-catalog reconciliation, and real "
                "OpenClaw/Hermes acceptance on the owning host. Requires confirm=true to run."
            ),
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "model": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "expected_served_model": {"type": "string"},
                    "expected_config_fingerprint": {"type": "string"},
                    "expected_router_config_sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "min_context_tokens": _bounded_integer_schema(
                        1, 10_000_000, 1
                    ),
                    "clients": {"type": "string"},
                    "openclaw_provider": {"type": "string"},
                    "hermes_provider": {"type": "string"},
                    "hermes_expected_provider": {"type": "string"},
                    "no_harness_sync": {"type": "boolean"},
                    "openclaw_config": {"type": "string"},
                    "pi_models": {"type": "string"},
                    "pi_settings": {"type": "string"},
                    "client_state_path": {"type": "string"},
                    "client_backup_root": {"type": "string"},
                    "timeout_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 3600,
                        "default": 600,
                    },
                    "run_id": {"type": "string"},
                    "output": {"type": "string"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                required=["base_url", "model", "expected_served_model"],
            ),
            "handler": tool_routed_eval,
        },
        "hermes_media_sync": {
            "description": (
                "Install the narrow Anvil media MCP server and packaged Hermes skill. "
                "Requires confirm=true to write."
            ),
            "inputSchema": _schema(
                {
                    "hermes_bin": {"type": "string"},
                    "hermes_home": {"type": "string"},
                    "hermes_profiles": {"type": "string"},
                    "skill_path": {"type": "string"},
                    "backup_root": {"type": "string"},
                    "anvil_command": {"type": "string"},
                    "mcp_url_env": {"type": "string"},
                    "token_env": {"type": "string"},
                    "restart_hermes_on_change": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 120, 15),
                }
            ),
            "handler": tool_hermes_media_sync,
        },
    },
)
