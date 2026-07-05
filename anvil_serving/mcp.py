"""anvil-serving MCP control plane.

This module exposes a small stdio JSON-RPC server for agent-facing operations
around ADR-0013: inspect the router/serves/host state, preview/apply OpenClaw
harness sync, restart the OpenClaw gateway, and run bounded probes. It is a
control plane only; model traffic still flows through ``anvil-serving serve``.

Runtime dependencies stay stdlib-only. Commands are argv lists, never shell
strings. Mutating tools require explicit ``confirm: true`` and keep dry-run
paths available.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, Optional


SERVER_INFO = {"name": "anvil-serving", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROXY_METHODS = {"tools/list", "tools/call"}


class ToolError(Exception):
    """User-facing tool failure rendered into the structured tool envelope."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _fail(code: str, message: str, details: Optional[dict] = None) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}


def _capture(fn: Callable[[], int]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fn()
    return rc, out.getvalue(), err.getvalue()


def _redact_secret(value: Any, token: str) -> Any:
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "<redacted>")
    if isinstance(value, list):
        return [_redact_secret(item, token) for item in value]
    if isinstance(value, dict):
        return {_redact_secret(key, token): _redact_secret(item, token) for key, item in value.items()}
    return value


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Optional[dict] = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": error}


def resolve_controller_token(auth_env: str, environ: Optional[dict[str, str]] = None) -> str:
    """Resolve a controller auth token from an env-var name, never a raw value."""

    if not auth_env or not _ENV_NAME_RE.fullmatch(auth_env):
        raise ToolError(
            "bad_auth_env",
            "auth-env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$",
            {"auth_env": auth_env},
        )
    env = os.environ if environ is None else environ
    token = (env.get(auth_env) or "").strip()
    if not token:
        raise ToolError("missing_auth_env", "auth env var is unset or empty", {"auth_env": auth_env})
    return token


def controller_auth_headers(token: str) -> dict[str, str]:
    """Headers accepted by the controller/front-door token gate."""

    return {
        "Authorization": "Bearer " + token,
        "x-api-key": token,
    }


def remote_controller_request(
    controller_url: str,
    request: dict,
    token: str,
    *,
    timeout: int = 30,
    opener: Optional[Callable[..., Any]] = None,
) -> dict:
    """POST one JSON-RPC request to a remote controller endpoint."""

    if not token:
        raise ToolError("missing_controller_token", "controller token is required")
    if opener is None:
        opener = urllib.request.urlopen
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **controller_auth_headers(token),
    }
    req = urllib.request.Request(controller_url, data=body, headers=headers, method="POST")
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        message = "controller returned HTTP %s" % exc.code
        details = {"status": exc.code}
        if raw:
            details["body"] = raw
        raise ToolError("controller_http_error", message, _redact_secret(details, token))
    except Exception as exc:
        raise ToolError(
            "controller_request_failed",
            _redact_secret(str(exc), token),
            {"controller_url": controller_url},
        )
    try:
        parsed = json.loads(raw or "{}")
    except ValueError as exc:
        raise ToolError("bad_controller_response", str(exc), {"controller_url": controller_url})
    if not isinstance(parsed, dict):
        raise ToolError("bad_controller_response", "controller response must be a JSON object")
    return _redact_secret(parsed, token)


def _arg_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _str_arg(args: dict, name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = args.get(name, default)
    if required and (value is None or value == ""):
        raise ToolError("missing_argument", "missing required argument %r" % name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ToolError("bad_argument", "%r must be a string" % name)
    return value


def _int_arg(args: dict, name: str, default: int) -> int:
    value = args.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("bad_argument", "%r must be an integer" % name)
    return value


def _command_preview(argv: list[str]) -> dict:
    return {"would_run": True, "command": argv}


def _probe_api_key_env(args: dict) -> str:
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    return _str_arg(args, "api_key_env", "")


def _run_argv(argv: list[str], *, confirm: bool, timeout: Optional[int] = None) -> dict:
    if not confirm:
        return _command_preview(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ToolError("command_not_found", str(exc), {"command": argv})
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", "command timed out", {"command": argv, "timeout": exc.timeout})
    return {
        "command": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def tool_router_status(args: dict) -> dict:
    from . import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    return _ok(router_manage.status_summary(container))


def tool_serves_status(args: dict) -> dict:
    from . import serves as serves_mod

    manifest = _str_arg(args, "manifest", serves_mod.DEFAULT_MANIFEST)
    names = args.get("names", [])
    if names is None:
        names = []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ToolError("bad_argument", "'names' must be an array of strings")
    try:
        serves = serves_mod.load_manifest(manifest)
    except FileNotFoundError:
        raise ToolError("manifest_not_found", "serves manifest not found", {"manifest": manifest})
    except Exception as exc:
        raise ToolError("bad_manifest", "could not load serves manifest", {"manifest": manifest, "error": str(exc)})
    return _ok(serves_mod.status_summary(serves, names))


def tool_doctor_summary(args: dict) -> dict:
    from . import doctor

    no_config = _arg_bool(args.get("no_config"), False)
    config = None if no_config else args.get("config", doctor.DEFAULT_CONFIG)
    if config is not None and not isinstance(config, str):
        raise ToolError("bad_argument", "'config' must be a string")
    return _ok(doctor.checks_summary(config_path=config, config_explicit=bool(args.get("config"))))


def _route_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/route") else base + "/route"


def tool_route_decision(args: dict) -> dict:
    base_url = _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    model = _str_arg(args, "model", "chat")
    prompt = _str_arg(args, "prompt", required=True)
    api_key_env = _str_arg(args, "api_key_env", "")
    timeout = _int_arg(args, "timeout_seconds", 5)
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key_env:
        token = os.environ.get(api_key_env)
        if token:
            headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_route_url(base_url), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw or "{}")
            return _ok({"status": getattr(resp, "status", resp.getcode()), "response": parsed})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        details: dict[str, Any] = {"status": exc.code, "body": raw}
        try:
            details["response"] = json.loads(raw)
        except ValueError:
            pass
        raise ToolError("route_http_error", "route decision returned HTTP %s" % exc.code, details)
    except Exception as exc:
        raise ToolError("route_probe_failed", str(exc), {"base_url": base_url})


def tool_openclaw_sync(args: dict) -> dict:
    from . import harness

    config = _str_arg(args, "config", required=True)
    base_url = _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    api_key_env = _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")
    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    gateway_path = _str_arg(args, "gateway_path", "~/.openclaw/openclaw.json")
    out = _str_arg(args, "out", "")
    overwrite = _arg_bool(args.get("overwrite"), False)
    restart = _arg_bool(args.get("restart"), False)
    dry_run = _arg_bool(args.get("dry_run"), True)
    confirm = _arg_bool(args.get("confirm"), False)

    try:
        preview = harness.openclaw_sync_preview(config, base_url=base_url, api_key_env=api_key_env)
    except FileNotFoundError:
        raise ToolError("config_not_found", "router config not found", {"config": config})
    except Exception as exc:
        raise ToolError("bad_config", "could not render OpenClaw config", {"config": config, "error": str(exc)})

    target = {
        "gateway_host": gateway_host or None,
        "gateway_user": gateway_user or None,
        "gateway_path": gateway_path,
        "out": out or None,
        "overwrite": overwrite,
        "restart": restart,
    }
    if dry_run or not confirm:
        return _ok({"applied": False, "target": target, "preview": preview})
    if not gateway_host and not out:
        raise ToolError(
            "missing_target",
            "openclaw sync apply requires gateway_host or out",
            {"target": target},
        )
    rc, stdout, stderr = _capture(lambda: harness.cmd_sync_openclaw(
        config,
        out=out or None,
        base_url=base_url,
        api_key_env=api_key_env,
        gateway_host=gateway_host or None,
        gateway_user=gateway_user or None,
        gateway_path=gateway_path,
        overwrite=overwrite,
        restart=restart,
    ))
    return _ok({
        "applied": rc == 0,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "target": target,
        "preview": {
            "model_count": preview["model_count"],
            "model_ids": preview["model_ids"],
            "plugin_id": preview["plugin_id"],
            "base_url": preview["base_url"],
            "api_key": preview["api_key"],
        },
    })


def tool_openclaw_gateway_restart(args: dict) -> dict:
    from . import harness

    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    dry_run = _arg_bool(args.get("dry_run"), True)
    confirm = _arg_bool(args.get("confirm"), False)
    argv = ["openclaw", "gateway", "restart"]
    if gateway_host:
        target = ("%s@%s" % (gateway_user, gateway_host)) if gateway_user else gateway_host
        argv = ["ssh", target, '$SHELL -lc "openclaw gateway restart"']
    if dry_run or not confirm:
        return _ok({"restarted": False, "dry_run": True, "command": argv})
    rc, stdout, stderr = _capture(lambda: harness.cmd_restart_openclaw(
        gateway_host=gateway_host or None,
        gateway_user=gateway_user or None,
    ))
    return _ok({"restarted": rc == 0, "returncode": rc, "stdout": stdout, "stderr": stderr})


def tool_preflight_probe(args: dict) -> dict:
    base_url = _str_arg(args, "base_url", required=True)
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    needle_ctx = _int_arg(args, "needle_ctx", 128000)
    tool_batch = _int_arg(args, "tool_batch", 20)
    no_thinking = _arg_bool(args.get("no_thinking"), False)
    confirm = _arg_bool(args.get("confirm"), False)
    argv = [sys.executable, "-m", "anvil_serving.preflight", "--base-url", base_url,
            "--model", model, "--needle-ctx", str(needle_ctx), "--tool-batch", str(tool_batch)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm))


def tool_benchmark_probe(args: dict) -> dict:
    base_url = _str_arg(args, "base_url", required=True)
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    requests = _int_arg(args, "requests", 60)
    concurrency = _int_arg(args, "concurrency", 20)
    max_tokens = _int_arg(args, "max_tokens", 64)
    ctx_tokens = _int_arg(args, "ctx_tokens", 0)
    no_thinking = _arg_bool(args.get("no_thinking"), False)
    confirm = _arg_bool(args.get("confirm"), False)
    argv = [sys.executable, "-m", "anvil_serving.benchmark", "--base-url", base_url,
            "--model", model, "--requests", str(requests), "--concurrency", str(concurrency),
            "--max-tokens", str(max_tokens), "--ctx-tokens", str(ctx_tokens)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm))


def _schema(properties: dict, required: Optional[list[str]] = None) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }


TOOLS: Dict[str, dict] = {
    "router_status": {
        "description": "Inspect the deployed anvil router container and loopback health.",
        "inputSchema": _schema({"container": {"type": "string"}}),
        "handler": tool_router_status,
    },
    "serves_status": {
        "description": "Inspect model serves from a serves.toml manifest.",
        "inputSchema": _schema({
            "manifest": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
        }),
        "handler": tool_serves_status,
    },
    "doctor_summary": {
        "description": "Run anvil-serving environment checks and return structured results.",
        "inputSchema": _schema({
            "config": {"type": "string"},
            "no_config": {"type": "boolean"},
        }),
        "handler": tool_doctor_summary,
    },
    "route_decision": {
        "description": "POST a prompt to the router /v1/route decision endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "api_key_env": {"type": "string"},
            "timeout_seconds": {"type": "integer"},
        }, required=["prompt"]),
        "handler": tool_route_decision,
    },
    "openclaw_sync": {
        "description": "Preview or apply OpenClaw harness config sync from a router config.",
        "inputSchema": _schema({
            "config": {"type": "string"},
            "base_url": {"type": "string"},
            "api_key_env": {"type": "string"},
            "gateway_host": {"type": "string"},
            "gateway_user": {"type": "string"},
            "gateway_path": {"type": "string"},
            "out": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "restart": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }, required=["config"]),
        "handler": tool_openclaw_sync,
    },
    "openclaw_gateway_restart": {
        "description": "Restart the OpenClaw gateway locally or over SSH. Requires confirm=true.",
        "inputSchema": _schema({
            "gateway_host": {"type": "string"},
            "gateway_user": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }),
        "handler": tool_openclaw_gateway_restart,
    },
    "preflight_probe": {
        "description": "Preview or run an anvil-serving preflight command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "needle_ctx": {"type": "integer"},
            "tool_batch": {"type": "integer"},
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }, required=["base_url", "model"]),
        "handler": tool_preflight_probe,
    },
    "benchmark_probe": {
        "description": "Preview or run an anvil-serving benchmark command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "requests": {"type": "integer"},
            "concurrency": {"type": "integer"},
            "max_tokens": {"type": "integer"},
            "ctx_tokens": {"type": "integer"},
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }, required=["base_url", "model"]),
        "handler": tool_benchmark_probe,
    },
}


def list_tools() -> list[dict]:
    return [{
        "name": name,
        "description": spec["description"],
        "inputSchema": spec["inputSchema"],
    } for name, spec in TOOLS.items()]


def call_tool(name: str, arguments: Optional[dict] = None) -> dict:
    if name not in TOOLS:
        return _fail("unknown_tool", "unknown tool %r" % name)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return _fail("bad_arguments", "tool arguments must be an object")
    try:
        return TOOLS[name]["handler"](arguments)
    except ToolError as exc:
        return _fail(exc.code, exc.message, exc.details)
    except Exception as exc:
        return _fail("internal_error", str(exc))


def _tool_result(envelope: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(envelope, sort_keys=True)}],
        "structuredContent": envelope,
        "isError": not envelope.get("ok", False),
    }


def handle_request(request: dict) -> Optional[dict]:
    req_id = request.get("id")
    method = request.get("method")
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": SERVER_INFO,
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": list_tools()}
        elif method == "tools/call":
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise ToolError("bad_params", "params must be an object")
            result = _tool_result(call_tool(params.get("name"), params.get("arguments") or {}))
        elif method == "notifications/initialized":
            return None
        else:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except ToolError as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": exc.message, "data": {"code": exc.code, **exc.details}},
        }


def handle_proxy_request(request: dict, controller_url: str, token: str) -> Optional[dict]:
    if request.get("method") not in _PROXY_METHODS:
        return handle_request(request)
    req_id = request.get("id")
    try:
        response = remote_controller_request(controller_url, request, token)
    except ToolError as exc:
        if req_id is None:
            return None
        return _jsonrpc_error(
            req_id,
            -32000,
            exc.message,
            {"code": exc.code, **exc.details},
        )
    if req_id is None:
        return None
    return response


def serve_stdio(
    stdin: Iterable[str] = sys.stdin,
    stdout: Any = sys.stdout,
    *,
    controller_url: str = "",
    controller_token: str = "",
) -> int:
    for line in stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        else:
            if not isinstance(request, dict):
                response = _jsonrpc_error(None, -32600, "request must be a JSON object")
            elif controller_url:
                response = handle_proxy_request(request, controller_url, controller_token)
            else:
                response = handle_request(request)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


def _parse_main_args(argv: list[str]) -> tuple[str, str, bool]:
    controller_url = ""
    auth_env = ""
    list_tools_requested = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--list-tools" or arg == "list-tools":
            list_tools_requested = True
            i += 1
        elif arg == "--controller-url":
            if i + 1 >= len(argv):
                raise ToolError("bad_usage", "--controller-url requires a value")
            controller_url = argv[i + 1]
            i += 2
        elif arg == "--auth-env":
            if i + 1 >= len(argv):
                raise ToolError("bad_usage", "--auth-env requires a value")
            auth_env = argv[i + 1]
            i += 2
        else:
            raise ToolError("bad_usage", "unknown argument %r" % arg)
    if list_tools_requested and (controller_url or auth_env):
        raise ToolError("bad_usage", "--list-tools cannot be combined with proxy mode")
    if bool(controller_url) != bool(auth_env):
        raise ToolError("bad_usage", "--controller-url and --auth-env must be provided together")
    return controller_url, auth_env, list_tools_requested


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        controller_url, auth_env, list_tools_requested = _parse_main_args(argv)
    except ToolError as exc:
        print("usage: anvil-serving mcp [--list-tools] [--controller-url URL --auth-env ENV]", file=sys.stderr)
        print(exc.message, file=sys.stderr)
        return 2
    if list_tools_requested:
        print(json.dumps({"tools": list_tools()}, indent=2, sort_keys=True))
        return 0
    if controller_url:
        try:
            token = resolve_controller_token(auth_env)
        except ToolError as exc:
            print("usage: anvil-serving mcp [--list-tools] [--controller-url URL --auth-env ENV]", file=sys.stderr)
            print(exc.message, file=sys.stderr)
            return 2
        return serve_stdio(controller_url=controller_url, controller_token=token)
    if argv:
        print("usage: anvil-serving mcp [--list-tools] [--controller-url URL --auth-env ENV]", file=sys.stderr)
        return 2
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
