"""Explicit router MCP tool family."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from ..arguments import (
    arg_bool as _arg_bool,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    probe_api_key_env as _probe_api_key_env,
    schema as _schema,
    str_arg as _str_arg,
)
from ..catalog import ToolFamily
from ..controller_client import (
    controller_auth_headers,
    http_error_details as _http_error_details,
    urlopen_no_proxy_no_redirect as _urlopen_no_proxy_no_redirect,
)
from ..errors import ToolError
from ..errors import ok as _ok
from ..runtime import (
    run_argv as _run_argv,
    run_argv_spooled as _run_argv_spooled,
)
from ..security import (
    redact_log_text as _redact_log_text,
    redact_secret as _redact_secret,
    safe_probe_url as _safe_probe_url,
)


def _router_manage_cli_argv(
    action: str,
    *,
    container: str = "",
    compose: str = "",
    service: str = "",
    env_file: str = "",
    dry_run: bool = False,
    no_verify: bool = False,
    recreate: bool = False,
    confirm: bool = False,
) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "router", action]
    if container:
        argv += ["--container", container]
    if compose:
        argv += ["--compose", compose]
    if service:
        argv += ["--service", service]
    if env_file:
        argv += ["--env-file", env_file]
    if dry_run:
        argv.append("--dry-run")
    if no_verify:
        argv.append("--no-verify")
    if recreate:
        argv.append("--recreate")
    if confirm:
        argv.append("--confirm")
    return argv


def tool_router_status(args: dict) -> dict:
    from .... import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    return _ok(
        router_manage.status_summary(
            container,
            _open=lambda url, timeout: urllib.request.urlopen(
                _safe_probe_url(url),
                timeout=timeout,
            ),
        )
    )


def tool_router_fleet_status(args: dict) -> dict:
    """Probe the exact installed router config from the router runtime."""
    from .... import router_manage

    timeout = _bounded_int_arg(args, "timeout", 4, min_value=1, max_value=60)
    try:
        report = router_manage.installed_fleet_status(
            container=router_manage.DEFAULT_CONTAINER,
            timeout=float(timeout),
        )
    except ValueError as exc:
        raise ToolError("router_fleet_status_failed", str(exc))
    return _ok(report)


def tool_router_logs(args: dict) -> dict:
    from .... import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    follow = _arg_bool(args.get("follow"), False, name="follow")
    if follow:
        raise ToolError(
            "follow_not_allowed", "router_logs rejects unbounded follow mode; use a bounded tail"
        )
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    max_output_bytes = _bounded_int_arg(
        args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
    )
    since = _str_arg(args, "since", "")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 60, min_value=1, max_value=600)
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "router",
        "logs",
        "--container",
        container,
        "--tail",
        str(tail),
    ]
    if since:
        argv += ["--since", since]
    result = _run_argv_spooled(
        argv,
        timeout=timeout_seconds,
        max_output_bytes=max_output_bytes,
        redactor=_redact_log_text,
    )
    return _ok(
        {
            "bounded": True,
            "tail": tail,
            "since": since or None,
            "max_output_bytes": max_output_bytes,
            **result,
        }
    )


def tool_router_manage(args: dict) -> dict:
    from .... import router_manage

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "restart", "reload"}:
        raise ToolError(
            "bad_action", "action must be one of: up, down, restart, reload", {"action": action}
        )
    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    compose_arg = _str_arg(args, "compose", "")
    service = _str_arg(args, "service", router_manage.DEFAULT_SERVICE)
    env_file_arg = _str_arg(args, "env_file", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    no_verify = _arg_bool(args.get("no_verify"), False, name="no_verify")
    recreate = _arg_bool(args.get("recreate"), False, name="recreate")
    if recreate and action != "up":
        raise ToolError("bad_argument", "'recreate' is only valid with action='up'")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)
    preview = dry_run or not confirm
    lifecycle = router_manage.lifecycle_plan(
        action,
        compose=compose_arg or None,
        service=service,
        env_file=env_file_arg or None,
        container=container,
        recreate=recreate,
    )
    argv = _router_manage_cli_argv(
        action,
        container=container if action in {"restart", "reload"} else "",
        compose=lifecycle["compose"] or "",
        service=lifecycle["service"] or "",
        env_file=lifecycle["env_file"] or "",
        dry_run=preview,
        no_verify=no_verify if action in {"restart", "reload"} else False,
        recreate=recreate,
        confirm=confirm,
    )
    target = {
        "action": action,
        "container": container,
        "compose": lifecycle["compose"],
        "compose_project": lifecycle["compose_project"],
        "service": lifecycle["service"],
        "env_file": lifecycle["env_file"],
        "recreate": recreate,
        "timeout_seconds": timeout_seconds,
        "no_verify": no_verify if action in {"restart", "reload"} else False,
    }
    if preview:
        return _ok(
            {
                "applied": False,
                "dry_run": True,
                "target": target,
                "command": argv,
                "lifecycle_command": lifecycle["command"],
            }
        )
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok(
        {
            "applied": True,
            "dry_run": False,
            "target": target,
            "lifecycle_command": lifecycle["command"],
            **result,
        }
    )


def tool_router_transition(args: dict) -> dict:
    from .... import router_manage

    action = _str_arg(args, "action", required=True)
    tier_id = _str_arg(args, "tier", "")
    router_url = _str_arg(args, "router_url", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout = args.get("timeout")
    if timeout is not None:
        timeout = _bounded_int_arg(args, "timeout", 60, min_value=1, max_value=3600)
    try:
        result = router_manage.transition_request(
            action,
            tier_id=tier_id or None,
            timeout=timeout,
            router_url=router_url or None,
            confirm=confirm,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise ToolError("transition_failed", str(exc))
    return _ok(result)


def _decision_records_from_path(path: str, *, max_input_bytes: int) -> list[dict]:
    if not os.path.isfile(path):
        raise ToolError(
            "decision_log_not_found", "decision summary source not found", {"path": path}
        )
    if os.path.getsize(path) > max_input_bytes:
        raise ToolError(
            "decision_log_too_large",
            "decision summary source exceeds max_input_bytes",
            {"path": path, "max_input_bytes": max_input_bytes},
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        records = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ToolError(
                    "bad_decision_log",
                    "bad JSONL decision record",
                    {"path": path, "line": lineno, "error": str(exc)},
                )
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return [item for item in parsed["records"] if isinstance(item, dict)]
        return [parsed]
    raise ToolError(
        "bad_decision_log",
        "decision summary source must be JSON array, JSONL, or object with records[]",
    )


def _decisions_url(base_url: str, limit: int) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/decisions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/decisions"
    else:
        url = base + "/v1/decisions"
    return url + "?" + urllib.parse.urlencode({"limit": str(limit)})


def tool_decision_summary(args: dict) -> dict:
    from ....router.decision_log import summarize_decisions

    limit = _bounded_int_arg(args, "limit", 20, min_value=1, max_value=500)
    max_input_bytes = _bounded_int_arg(
        args, "max_input_bytes", 1048576, min_value=1024, max_value=10485760
    )
    timeout = _bounded_int_arg(args, "timeout_seconds", 5, min_value=1, max_value=60)
    base_url = _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    api_key_env = _probe_api_key_env(args)
    path = _str_arg(args, "path", "")
    records_arg = args.get("records", [])
    if records_arg is None:
        records_arg = []
    if not isinstance(records_arg, list) or not all(isinstance(item, dict) for item in records_arg):
        raise ToolError("bad_argument", "'records' must be an array of objects")
    records = list(records_arg)
    source = "inline"
    if path:
        records = _decision_records_from_path(path, max_input_bytes=max_input_bytes)
        source = "path"
    if not path and not records:
        base_url = _safe_probe_url(base_url)
        token = ""
        headers = {"Accept": "application/json"}
        if api_key_env:
            token = os.environ.get(api_key_env)
            if token:
                headers.update(controller_auth_headers(token))
        req = urllib.request.Request(_decisions_url(base_url, limit), headers=headers, method="GET")
        try:
            with _urlopen_no_proxy_no_redirect(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            details, _ = _http_error_details(exc, token)
            raise ToolError(
                "decision_summary_http_error",
                "decision summary returned HTTP %s" % exc.code,
                details,
            )
        except Exception as exc:
            raise ToolError(
                "decision_summary_failed", _redact_secret(str(exc), token), {"base_url": base_url}
            )
        if not isinstance(parsed, dict):
            raise ToolError(
                "bad_decision_summary", "decision summary response must be a JSON object"
            )
        parsed = _redact_secret(parsed, token)
        parsed["source"] = "router"
        parsed["base_url"] = base_url
        return _ok(parsed)
    summary = summarize_decisions(records, limit=limit)
    summary["source"] = source
    summary["path"] = path or None
    return _ok(summary)


FAMILY = ToolFamily(
    name="router",
    tools={
        "router_status": {
            "description": "Inspect the deployed anvil router container and loopback health.",
            "inputSchema": _schema({"container": {"type": "string"}}),
            "handler": tool_router_status,
        },
        "router_fleet_status": {
            "description": "Probe the installed router configuration from the live router runtime without returning private endpoint identities.",
            "inputSchema": _schema(
                {
                    "timeout": _bounded_integer_schema(1, 60, 4),
                }
            ),
            "handler": tool_router_fleet_status,
        },
        "router_logs": {
            "description": "Read bounded, redacted docker logs for the deployed router; follow mode is not allowed.",
            "inputSchema": _schema(
                {
                    "container": {"type": "string"},
                    "tail": _bounded_integer_schema(1, 5000, 200),
                    "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
                    "since": {"type": "string"},
                    "follow": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 600, 60),
                }
            ),
            "handler": tool_router_logs,
        },
        "router_manage": {
            "description": "Preview or run guarded deployed-router lifecycle actions: up, down, restart, or reload.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "container": {"type": "string"},
                    "compose": {"type": "string"},
                    "service": {"type": "string"},
                    "env_file": {"type": "string"},
                    "recreate": {"type": "boolean"},
                    "no_verify": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                    "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
                },
                required=["action"],
            ),
            "handler": tool_router_manage,
        },
        "router_transition": {
            "description": "Inspect, quiesce, drain, or safely readmit a router tier through the authenticated router boundary.",
            "inputSchema": _schema(
                {
                    "action": {"type": "string"},
                    "tier": {"type": "string"},
                    "router_url": {"type": "string"},
                    "timeout": _bounded_integer_schema(1, 3600, 60),
                    "dry_run": {"type": "boolean"},
                    "confirm": {"type": "boolean"},
                },
                required=["action"],
            ),
            "handler": tool_router_transition,
        },
        "decision_summary": {
            "description": "Summarize recent router decisions without prompts or secrets; defaults to GET /v1/decisions.",
            "inputSchema": _schema(
                {
                    "base_url": {"type": "string"},
                    "api_key_env": {"type": "string"},
                    "records": {"type": "array", "items": {"type": "object"}},
                    "path": {"type": "string"},
                    "limit": _bounded_integer_schema(1, 500, 20),
                    "max_input_bytes": _bounded_integer_schema(1024, 10485760, 1048576),
                    "timeout_seconds": _bounded_integer_schema(1, 60, 5),
                }
            ),
            "handler": tool_decision_summary,
        },
    },
)
