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
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, Optional


SERVER_INFO = {"name": "anvil-serving", "version": "0.1.0"}
PROTOCOL_VERSION = "2024-11-05"
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROXY_METHODS = {"tools/list", "tools/call"}
_PROBE_API_KEY_ENVS = {"ANVIL_ROUTER_TOKEN"}
_MAX_ERROR_BODY_BYTES = 4096


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urlopen_no_proxy_no_redirect(req, timeout=30):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(req, timeout=timeout)


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


def _http_error_details(exc: urllib.error.HTTPError, token: str = "") -> tuple[dict[str, Any], str]:
    details: dict[str, Any] = {"status": exc.code}
    if 300 <= exc.code < 400:
        location = exc.headers.get("Location") if exc.headers else None
        if location:
            details["location"] = location
        return _redact_secret(details, token), ""

    raw = ""
    try:
        body = exc.read(_MAX_ERROR_BODY_BYTES + 1)
    except Exception as body_exc:
        details["body_error"] = str(body_exc)
    else:
        if body:
            truncated = len(body) > _MAX_ERROR_BODY_BYTES
            raw = body[:_MAX_ERROR_BODY_BYTES].decode("utf-8", "replace")
            details["body"] = raw
            if truncated:
                details["body_truncated"] = True
    return _redact_secret(details, token), raw


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
    controller_url = _safe_controller_url(controller_url)
    if opener is None:
        opener = _urlopen_no_proxy_no_redirect
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
        details, _ = _http_error_details(exc, token)
        message = "controller returned HTTP %s" % exc.code
        raise ToolError("controller_http_error", message, details)
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


def _arg_bool(value: Any, default: bool = False, *, name: str = "argument") -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ToolError("bad_argument", "%r must be a boolean" % name)


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


def _bounded_int_arg(args: dict, name: str, default: int, *, min_value: int, max_value: int) -> int:
    value = _int_arg(args, name, default)
    if value < min_value or value > max_value:
        raise ToolError(
            "bad_argument",
            "%r must be between %d and %d" % (name, min_value, max_value),
            {"value": value},
        )
    return value


def _is_tailscale_v4(addr: str) -> bool:
    # ipaddress treats 100.64.0.0/10 as special rather than private on some
    # Python versions. Keep the controller/probe tailnet allowance explicit.
    try:
        import ipaddress
        ip = ipaddress.ip_address(addr)
        if ip.version == 4:
            return ip in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False
    return False


def _is_safe_probe_ip(addr: str) -> bool:
    import ipaddress

    ip = ipaddress.ip_address(addr)
    if ip.is_unspecified or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return False
    if ip.version == 4:
        rfc1918 = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        return bool(ip.is_loopback or _is_tailscale_v4(addr) or any(ip in network for network in rfc1918))
    return bool(ip.is_loopback or ip in ipaddress.ip_network("fc00::/7"))


def _safe_probe_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("bad_base_url", "base_url must be an http(s) URL with a host")
    if parsed.hostname.strip().lower() == "localhost":
        raise ToolError("bad_base_url", "use 127.0.0.1 or a private/tailnet host, not localhost")
    host = parsed.hostname
    try:
        if not _is_safe_probe_ip(host):
            raise ToolError(
                "unsafe_base_url",
                "probe base_url must resolve to loopback, private, or tailnet addresses",
                {"host": host},
            )
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ToolError("bad_base_url", "could not resolve base_url host", {"host": host, "error": str(exc)})
        addrs = []
        for info in infos:
            try:
                addrs.append(info[4][0])
            except (IndexError, TypeError):
                pass
        if not addrs or any(not _is_safe_probe_ip(addr) for addr in addrs):
            raise ToolError(
                "unsafe_base_url",
                "probe base_url must resolve only to loopback, private, or tailnet addresses",
                {"host": host, "addresses": addrs},
            )
    return base_url


def _safe_controller_url(controller_url: str) -> str:
    return _safe_probe_url(controller_url)


def _command_preview(argv: list[str]) -> dict:
    return {"would_run": True, "command": argv}


def _probe_api_key_env(args: dict) -> str:
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = _str_arg(args, "api_key_env", "")
    if not api_key_env:
        return ""
    if not _ENV_NAME_RE.fullmatch(api_key_env):
        raise ToolError("bad_api_key_env", "api_key_env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$")
    if api_key_env not in _PROBE_API_KEY_ENVS:
        raise ToolError(
            "unsafe_api_key_env",
            "api_key_env must be ANVIL_ROUTER_TOKEN for MCP probe tools",
            {"api_key_env": api_key_env},
        )
    return api_key_env


def _run_argv(argv: list[str], *, confirm: bool, timeout: Optional[int] = None) -> dict:
    if not confirm:
        return _command_preview(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise ToolError("command_not_found", str(exc), {"command": argv})
    except subprocess.TimeoutExpired as exc:
        raise ToolError("timeout", "command timed out", {"command": argv, "timeout": exc.timeout})
    result = {
        "command": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }
    if proc.returncode != 0:
        raise ToolError("command_failed", "command exited with status %s" % proc.returncode, result)
    return result


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

    no_config = _arg_bool(args.get("no_config"), False, name="no_config")
    config = None if no_config else args.get("config", doctor.DEFAULT_CONFIG)
    if config is not None and not isinstance(config, str):
        raise ToolError("bad_argument", "'config' must be a string")
    return _ok(doctor.checks_summary(config_path=config, config_explicit=bool(args.get("config"))))


def tool_models_inventory(args: dict) -> dict:
    from . import models

    catalog_dir = _str_arg(args, "catalog_dir", "model-library")
    hf_roots = _str_arg(args, "hf_roots", "")
    model_dirs = _str_arg(args, "model_dirs", "")
    sync = _arg_bool(args.get("sync"), False, name="sync")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 1800, min_value=1, max_value=7200)
    argv = models.build_sync_argv(catalog_dir, hf_roots=hf_roots, model_dirs=model_dirs)
    if sync:
        if not confirm:
            return _ok({
                "synced": False,
                "dry_run": True,
                "catalog_dir": os.path.abspath(catalog_dir),
                "command": argv,
            })
        run_result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
        try:
            inventory = models.load_model_catalog(catalog_dir)
        except models.CatalogNotFound as exc:
            raise ToolError(
                "catalog_not_found",
                "models sync completed but no catalog was found; check sync output and --out",
                {"catalog_dir": exc.catalog_dir, "command": argv, "stdout": run_result.get("stdout", ""), "stderr": run_result.get("stderr", "")},
            )
        except models.CatalogError as exc:
            raise ToolError("bad_catalog", str(exc), exc.details)
        return _ok({
            "synced": True,
            "dry_run": False,
            "command": argv,
            "returncode": run_result["returncode"],
            "stdout": run_result["stdout"],
            "stderr": run_result["stderr"],
            "catalog": inventory,
        })

    try:
        inventory = models.load_model_catalog(catalog_dir)
    except models.CatalogNotFound as exc:
        raise ToolError(
            "catalog_not_found",
            "model catalog not found; run the command from error.details.command first",
            {"catalog_dir": exc.catalog_dir, "command": argv},
        )
    except models.CatalogError as exc:
        raise ToolError("bad_catalog", str(exc), exc.details)
    return _ok({"synced": False, "dry_run": False, "catalog": inventory})


def _route_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/route") else base + "/route"


def tool_route_decision(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", "http://127.0.0.1:8000/v1"))
    model = _str_arg(args, "model", "chat")
    prompt = _str_arg(args, "prompt", required=True)
    api_key_env = _probe_api_key_env(args)
    timeout = _bounded_int_arg(args, "timeout_seconds", 5, min_value=1, max_value=60)
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = ""
    if api_key_env:
        token = os.environ.get(api_key_env)
        if token:
            headers["Authorization"] = "Bearer " + token
            headers["x-api-key"] = token
    req = urllib.request.Request(_route_url(base_url), data=body, headers=headers, method="POST")
    try:
        with _urlopen_no_proxy_no_redirect(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw or "{}")
            return _ok(_redact_secret(
                {"status": getattr(resp, "status", resp.getcode()), "response": parsed},
                token,
            ))
    except urllib.error.HTTPError as exc:
        details, raw = _http_error_details(exc, token)
        try:
            details["response"] = _redact_secret(json.loads(raw), token)
        except ValueError:
            pass
        if exc.code == 503:
            return _fail("no_available_tier", "route decision returned HTTP 503", details)
        raise ToolError("route_http_error", "route decision returned HTTP %s" % exc.code, details)
    except Exception as exc:
        raise ToolError("route_probe_failed", _redact_secret(str(exc), token), {"base_url": base_url})


def tool_openclaw_sync(args: dict) -> dict:
    from . import harness

    config = _str_arg(args, "config", required=True)
    base_url = _safe_probe_url(_str_arg(args, "base_url", "http://127.0.0.1:8000/v1"))
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = _probe_api_key_env({"api_key_env": _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")})
    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    gateway_path = _str_arg(args, "gateway_path", "~/.openclaw/openclaw.json")
    out = _str_arg(args, "out", "")
    overwrite = _arg_bool(args.get("overwrite"), False, name="overwrite")
    restart = _arg_bool(args.get("restart"), False, name="restart")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    if gateway_host:
        try:
            harness._validate_gateway_target(gateway_host, gateway_user)
        except ValueError as exc:
            raise ToolError("bad_gateway_target", str(exc), {"gateway_host": gateway_host, "gateway_user": gateway_user})

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
        "timeout_seconds": timeout_seconds,
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
        timeout_seconds=timeout_seconds,
    ))
    result = {
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
    }
    if rc != 0:
        raise ToolError("command_failed", "openclaw sync exited with status %s" % rc, result)
    return _ok(result)


def tool_openclaw_gateway_restart(args: dict) -> dict:
    from . import harness

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
            raise ToolError("bad_gateway_target", str(exc), {"gateway_host": gateway_host, "gateway_user": gateway_user})
        target = ("%s@%s" % (gateway_user, gateway_host)) if gateway_user else gateway_host
        argv = ["ssh", *harness._ssh_options(timeout_seconds), "--", target, harness._REMOTE_RESTART_COMMAND]
    if dry_run or not confirm:
        return _ok({"restarted": False, "dry_run": True, "command": argv})
    rc, stdout, stderr = _capture(lambda: harness.cmd_restart_openclaw(
        gateway_host=gateway_host or None,
        gateway_user=gateway_user or None,
        timeout_seconds=timeout_seconds,
    ))
    result = {"restarted": rc == 0, "returncode": rc, "stdout": stdout, "stderr": stderr}
    if rc != 0:
        raise ToolError("command_failed", "openclaw restart exited with status %s" % rc, result)
    return _ok(result)


def tool_preflight_probe(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    needle_ctx = _bounded_int_arg(args, "needle_ctx", 128000, min_value=1, max_value=262144)
    tool_batch = _bounded_int_arg(args, "tool_batch", 20, min_value=1, max_value=100)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 1800, min_value=1, max_value=7200)
    argv = [sys.executable, "-m", "anvil_serving.preflight", "--base-url", base_url,
            "--model", model, "--needle-ctx", str(needle_ctx), "--tool-batch", str(tool_batch)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm, timeout=timeout_seconds))


def tool_benchmark_probe(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    requests = _bounded_int_arg(args, "requests", 60, min_value=1, max_value=200)
    concurrency = _bounded_int_arg(args, "concurrency", 20, min_value=1, max_value=100)
    max_tokens = _bounded_int_arg(args, "max_tokens", 64, min_value=1, max_value=4096)
    ctx_tokens = _bounded_int_arg(args, "ctx_tokens", 0, min_value=0, max_value=262144)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 1800, min_value=1, max_value=7200)
    argv = [sys.executable, "-m", "anvil_serving.benchmark", "--base-url", base_url,
            "--model", model, "--requests", str(requests), "--concurrency", str(concurrency),
            "--max-tokens", str(max_tokens), "--ctx-tokens", str(ctx_tokens)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm, timeout=timeout_seconds))


def _schema(properties: dict, required: Optional[list[str]] = None) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }


def _bounded_integer_schema(minimum: int, maximum: int, default: int) -> dict:
    return {"type": "integer", "minimum": minimum, "maximum": maximum, "default": default}


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
    "models_inventory": {
        "description": "Read the generated model catalog, or preview/run `models sync` to create it.",
        "inputSchema": _schema({
            "catalog_dir": {"type": "string"},
            "hf_roots": {"type": "string"},
            "model_dirs": {"type": "string"},
            "sync": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
        }),
        "handler": tool_models_inventory,
    },
    "route_decision": {
        "description": "POST a prompt to the router /v1/route decision endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "prompt": {"type": "string"},
            "api_key_env": {"type": "string"},
            "timeout_seconds": _bounded_integer_schema(1, 60, 5),
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
            "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
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
            "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
        }),
        "handler": tool_openclaw_gateway_restart,
    },
    "preflight_probe": {
        "description": "Preview or run an anvil-serving preflight command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "needle_ctx": _bounded_integer_schema(1, 262144, 128000),
            "tool_batch": _bounded_integer_schema(1, 100, 20),
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
        }, required=["base_url", "model"]),
        "handler": tool_preflight_probe,
    },
    "benchmark_probe": {
        "description": "Preview or run an anvil-serving benchmark command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "requests": _bounded_integer_schema(1, 200, 60),
            "concurrency": _bounded_integer_schema(1, 100, 20),
            "max_tokens": _bounded_integer_schema(1, 4096, 64),
            "ctx_tokens": _bounded_integer_schema(0, 262144, 0),
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
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
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if "id" not in request:
        return None
    req_id = request.get("id")
    if req_id is None:
        return _jsonrpc_error(None, -32600, "id must not be null")
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
            params = request.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise ToolError("bad_params", "params must be an object")
            if params.get("name") not in TOOLS:
                raise ToolError("unknown_tool", "unknown tool %r" % params.get("name"))
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ToolError("bad_arguments", "tool arguments must be an object")
            result = _tool_result(call_tool(params.get("name"), arguments))
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
    if "id" not in request:
        return None
    req_id = request.get("id")
    if req_id is None:
        return _jsonrpc_error(None, -32600, "id must not be null")
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
            controller_url = _safe_controller_url(controller_url)
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
