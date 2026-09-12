"""Read-only, bounded request diagnostics; never replay model traffic."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import stat
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from . import envfile
from .operator_output import CommandResult, OperatorError, TransportError, UsageError
from .paths import config_path
from .router.decision_log import safe_client_id, safe_correlation, safe_gateway_request_id

MAX_RESPONSE_BYTES = 128 * 1024
MAX_CONFIG_BYTES = 32 * 1024
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SECRET = re.compile(r"(?i)(?:bearer|sk-|hf_|token|secret|password|api.key)")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_CONFIG_KEYS = frozenset({"router_url", "auth_env", "credential_env_file", "timeout"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _config_error() -> UsageError:
    """Return one content-free error for an unusable local diagnostics file."""
    return UsageError("Router diagnostics configuration is invalid.", code="router_diagnostics_config_invalid")


def _read_bounded_toml(path: Path, *, missing_ok: bool) -> dict:
    """Read one small regular TOML file; callers never display its path or data."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            raise _config_error()
        with path.open("rb") as handle:
            raw = handle.read(MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise _config_error() from None
    except OSError:
        raise _config_error() from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise _config_error()
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        raise _config_error() from None
    if not isinstance(value, dict) or set(value) - _CONFIG_KEYS:
        raise _config_error()
    return value


def _credential_file(value: object, config: Path) -> Path:
    """Resolve only an operator-selected credential file beside this config."""
    if not isinstance(value, str) or not value or len(value) > 1024 or any(ord(char) <= 32 for char in value):
        raise _config_error()
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = config.parent / candidate
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        raise _config_error() from None
    # This command intentionally does not participate in envfile's shared
    # fallback chain. A per-command file may be selected in its TOML only.
    if candidate == Path.home() / ".env":
        raise _config_error()
    return candidate


def _load_cli_settings(path: str | None) -> tuple[dict, Path | None]:
    """Load strict, optional per-command defaults from the operator config home."""
    explicit = path is not None
    if explicit and (not isinstance(path, str) or not path or len(path) > 1024):
        raise _config_error()
    source = Path(path).expanduser() if explicit else Path(config_path("router-diagnostics.toml"))
    value = _read_bounded_toml(source, missing_ok=not explicit)
    if not value:
        return {}, None
    settings = {}
    router_url = value.get("router_url")
    if router_url is not None:
        try:
            settings["router_url"] = _router_url(router_url)
        except UsageError:
            raise _config_error() from None
    auth_env = value.get("auth_env")
    if auth_env is not None:
        if not isinstance(auth_env, str) or _ENV_NAME.fullmatch(auth_env) is None:
            raise _config_error()
        settings["auth_env"] = auth_env
    timeout = value.get("timeout")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise _config_error()
        settings["timeout"] = float(timeout)
    credential = value.get("credential_env_file")
    if credential is not None:
        settings["credential_env_file"] = _credential_file(credential, source)
    return settings, source


def _read_configured_credential(path: Path, name: str) -> str | None:
    """Read a bounded, explicit dotenv file without the shared fallback chain."""
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            raise _config_error()
        values = envfile.read_dotenv(path)
    except (OSError, UnicodeError):
        raise _config_error() from None
    value = values.get(name)
    return value if isinstance(value, str) and value else None


def _dispatch_settings(args) -> tuple[str, str | None, float]:
    """Apply CLI, process, and optional config defaults without exposing secrets."""
    settings, _ = _load_cli_settings(args.config)
    router_url = (
        args.router_url if args.router_url is not None
        else os.environ.get("ANVIL_ROUTER_URL") or settings.get("router_url")
        or "http://127.0.0.1:8000"
    )
    auth_env = args.auth_env if args.auth_env is not None else settings.get("auth_env") or "ANVIL_ROUTER_TOKEN"
    timeout = args.timeout if args.timeout is not None else settings.get("timeout", 5.0)
    if not isinstance(auth_env, str) or _ENV_NAME.fullmatch(auth_env) is None:
        raise _config_error()
    token = os.environ.get(auth_env)
    credential_file = settings.get("credential_env_file")
    if not token and isinstance(credential_file, Path):
        token = _read_configured_credential(credential_file, auth_env)
    return router_url, token, timeout


def _router_url(value: str) -> str:
    """Accept an explicit origin, without userinfo, URL secrets, or redirects."""
    try:
        if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or "\\" in value or parsed.port == 0
        ):
            raise ValueError
        if parsed.scheme == "http":
            address = ipaddress.ip_address(parsed.hostname)
            if not (
                address.is_loopback or address in ipaddress.ip_network("10.0.0.0/8")
                or address in ipaddress.ip_network("172.16.0.0/12")
                or address in ipaddress.ip_network("192.168.0.0/16")
                or address in ipaddress.ip_network("100.64.0.0/10")
                or address in ipaddress.ip_network("fc00::/7")
            ):
                raise ValueError
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    except (ValueError, TypeError):
        raise UsageError(
            "Use an HTTPS router origin or an HTTP private/loopback IP, without a path or URL credentials.",
            code="invalid_router_url",
        ) from None


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 10**15 or not math.isfinite(value):
        return None
    if integer and not isinstance(value, int):
        return None
    return value


def _label(value):
    if isinstance(value, str) and _LABEL.fullmatch(value) and not _SECRET.search(value):
        return value
    return None


def _client_label(value):
    return value if safe_client_id(value) is not None else None


def _fetch(base, path, token, timeout, opener):
    request = urllib.request.Request(
        base + path, headers={"Accept": "application/json", "Authorization": "Bearer " + token},
        method="GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            if response.status != 200:
                raise TransportError("Router returned an unexpected status.", code="router_http_error")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if status == 404:
            raise OperatorError(
                "Request record is unavailable; it may be active, evicted, from a previous process, or unsupported by this router.",
                code="request_not_found",
            ) from None
        if status in {401, 403}:
            raise TransportError("Router denied diagnostic access.", code="router_access_denied") from None
        raise TransportError("Router returned an HTTP error.", code="router_http_error") from None
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException):
        raise TransportError("Router diagnostic transport failed.", code="router_unreachable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TransportError("Router diagnostic response exceeded the size bound.", code="router_response_oversized")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise TransportError("Router diagnostic response was malformed.", code="router_response_invalid") from None
    if not isinstance(value, dict):
        raise TransportError("Router diagnostic response was malformed.", code="router_response_invalid")
    return value


def diagnose_record(record: Mapping, *, active: bool = False) -> dict:
    """Project known metadata and derive bounded observations, never content."""
    attempts = record.get("attempts")
    attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
    if not isinstance(attempt, Mapping):
        attempt = {}
    reason = attempt.get("reason")
    if not isinstance(reason, str):
        reason = None
    succeeded = attempt.get("succeeded") is True
    upstream_outcome = "succeeded" if succeeded else "failed" if attempts else "unknown"
    delivery_outcome = record.get("workload_outcome")
    if delivery_outcome not in {"success", "error", "cancelled", "timeout", "rejected", "disconnected"}:
        delivery_outcome = None
    # The active workload endpoint has no terminal delivery outcome.  An
    # absent upstream attempt there means it is still progressing, not failed.
    ongoing = active and delivery_outcome is None
    measurements = record.get("measurements")
    if not isinstance(measurements, Mapping):
        measurements = {}
    finish = measurements.get("finish_reason")
    if not isinstance(finish, str) or finish not in {"stop", "length", "tool_calls", "content_filter", "unknown"}:
        finish = None
    timing = {
        key: _number(record.get(key) if key == "latency_ms" else measurements.get(key)) for key in (
            "latency_ms", "upstream_duration_ms", "time_to_first_content_ms", "readiness_check_ms",
        )
    }
    # Purpose records retain a legacy zero default, not an elapsed measurement.
    if record.get("kind") in ("embedding", "rerank"):
        timing = {key: None for key in timing}
    elapsed = timing["latency_ms"]
    if elapsed is not None:
        for phase in ("readiness_check_ms", "upstream_duration_ms", "time_to_first_content_ms"):
            if timing[phase] is not None and timing[phase] > elapsed:
                timing[phase] = None
    observations = []
    checks = []
    if ongoing:
        observations.append("waiting_for_upstream_activity")
        checks.append("wait_for_current_phase_or_terminal_outcome")
    elif not succeeded and reason in {"over_context", "media_admission_context_limit"}:
        upstream_outcome = "not_attempted"
        observations.append("request_rejected_over_context")
        checks.extend(("check_request_context_and_media_limits",
                       "compare_serialized_request_with_client_context_estimate"))
    elif not succeeded:
        observations.append("request_failed")
        if reason in {"quiesced", "unavailable", "upstream_metadata_unavailable", "backend_unbound"}:
            checks.append("check_selected_tier_readiness_and_admission")
        elif reason == "client_disconnected":
            checks.append("check_client_timeout_or_cancellation")
        else:
            checks.append("inspect_selected_upstream_logs_using_request_id")
    if delivery_outcome in {"disconnected", "cancelled", "timeout"}:
        observations.append("delivery_" + delivery_outcome)
        checks.append("check_client_timeout_or_cancellation")
    if finish == "length":
        observations.append("output_limit_reached")
        checks.append("compare_output_limit_with_visible_answer_and_reasoning_budget")
    elif finish == "tool_calls":
        observations.append("model_requested_tool_execution")
    first = timing["time_to_first_content_ms"]
    if first is not None and elapsed:
        observations.append("startup_dominated" if first > elapsed / 2 else "completion_dominated")
    usage = record.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}
    safe_usage = {
        "prompt_tokens": _number(usage.get("prompt_tokens", record.get("total_prompt_tokens")), integer=True),
        "completion_tokens": _number(usage.get("completion_tokens", record.get("total_completion_tokens")), integer=True),
        "prompt_source": usage.get("prompt_source") if usage.get("prompt_source") in ("upstream", "estimated", "unknown") else "unknown",
        "completion_source": usage.get("completion_source") if usage.get("completion_source") in ("upstream", "estimated", "unknown") else "unknown",
    }
    limit = record.get("output_limit")
    if not isinstance(limit, Mapping):
        limit = {}
    safe_limit = {
        "requested": _number(limit.get("requested"), integer=True),
        "applied": _number(limit.get("applied"), integer=True),
        "clamped": limit.get("clamped") if isinstance(limit.get("clamped"), bool) else None,
    }
    if safe_limit["clamped"] is True and not (
        safe_limit["requested"] is not None and safe_limit["applied"] is not None
        and 0 < safe_limit["applied"] < safe_limit["requested"]
    ):
        safe_limit = {"requested": None, "applied": None, "clamped": None}
    return {
        "request_id": _label(record.get("request_id")),
        "gateway_request_id": _label(record.get("gateway_request_id")),
        "route": _label(record.get("route")),
        "requested_tier": _label(record.get("requested_tier")) or _label(record.get("tier_id")),
        "served_tier": _label(record.get("served_tier")),
        "outcome": delivery_outcome or ("ongoing" if ongoing else upstream_outcome),
        "upstream_outcome": upstream_outcome,
        "delivery_outcome": delivery_outcome,
        "finish_reason": finish,
        "timing": timing,
        "usage": safe_usage,
        "output_limit": safe_limit,
        "session_id": _label(record.get("session_id")),
        "client_id": _client_label(record.get("client_id")),
        "cache_read_input_tokens": _number(record.get("cache_read_input_tokens"), integer=True),
        "admission_wait_ms": _number(record.get("admission_wait_ms"), integer=True),
        "request_router": {
            "config_sha256": record.get("config_sha256") if isinstance(record.get("config_sha256"), str) and _HEX.fullmatch(record.get("config_sha256")) else None,
            "version": _label(record.get("router_version")),
        },
        "active": {
            "state": _label(record.get("state")),
            "phase": _label(record.get("phase")),
            "elapsed_ms": _number(record.get("elapsed_ms"), integer=True),
            "last_activity_ms": _number(record.get("last_activity_ms"), integer=True),
            "created_at": _label(record.get("created_at")),
            "estimated_input_tokens": _number(record.get("estimated_input_tokens"), integer=True),
            "context_limit_tokens": _number(record.get("context_limit_tokens"), integer=True),
            "input_tokens": _number(record.get("input_tokens"), integer=True),
            "output_tokens": _number(record.get("output_tokens"), integer=True),
            "cache_read_input_tokens": _number(record.get("cache_read_input_tokens"), integer=True),
        },
        "observations": observations,
        "next_checks": checks,
    }


def diagnose_session(session_id=None, *, router_url, token, active=False, timeout=5.0, _open=None) -> dict:
    """Read one bounded metadata-only session view; never replay model traffic."""
    if session_id is not None and (
        not isinstance(session_id, str) or safe_correlation(session_id) is None or _label(session_id) is None
    ):
        raise UsageError("Use a bounded opaque session identifier.", code="invalid_session_id")
    if not isinstance(active, bool) or (session_id is None and not active):
        raise UsageError("Active must be a boolean.")
    base = _router_url(router_url)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise UsageError("Timeout must be greater than zero and at most 30 seconds.")
    if not isinstance(token, str) or not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise UsageError("A router credential is required in the selected environment variable.", code="router_credential_required")
    opener = _open or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
    flag = "active" if active else "history"
    params = {}
    if session_id is not None:
        params["session_id"] = session_id
    params[flag] = "1"
    query = urllib.parse.urlencode(params)
    trace = _fetch(base, "/v1/requests?" + query, token, timeout, opener)
    expected_scope = "active_workload_buffer" if active else "decision_log_jsonl"
    records = trace.get("records")
    if (
        trace.get("object") != "router_request_history"
        or trace.get("scope") != expected_scope
        or (session_id is not None and trace.get("session_id") != session_id)
        or not isinstance(records, list)
        or len(records) > 50
        or any(not isinstance(record, Mapping) for record in records)
    ):
        raise TransportError("Router returned a malformed session diagnostic.", code="router_response_invalid")
    if not active and any(record.get("session_id") != session_id for record in records):
        raise TransportError("Router returned mismatched session records.", code="router_response_invalid")
    return {
        "schema": "anvil-router-diagnosis/v1",
        "scope": expected_scope,
        "session_id": session_id,
        "active": active,
        "requests": [diagnose_record(record, active=active) for record in records],
        "truncated": trace.get("truncated") is True,
        "limitations": [
            "metadata_only", "history_is_bounded_to_managed_jsonl_generations",
            "active_view_is_a_current_snapshot" if active else "terminal_records_only",
        ],
    }


def diagnose_request(request_id, *, router_url, token, timeout=5.0, _open=None) -> dict:
    """Read one terminal request plus separately labelled current process metadata."""
    if not isinstance(request_id, str) or safe_correlation(request_id) is None or _label(request_id) is None:
        raise UsageError("Use a bounded opaque request identifier.", code="invalid_request_id")
    base = _router_url(router_url)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise UsageError("Timeout must be greater than zero and at most 30 seconds.")
    if not isinstance(token, str) or not token or any(ord(c) <= 32 or ord(c) >= 127 for c in token):
        raise UsageError("A router credential is required in the selected environment variable.", code="router_credential_required")
    opener = _open or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
    trace = _fetch(base, "/v1/requests/" + urllib.parse.quote(request_id, safe=""), token, timeout, opener)
    record = trace.get("record")
    if (
        trace.get("object") != "router_request"
        or trace.get("scope") not in {"current_decision_log_buffer", "decision_log_jsonl"}
        or not isinstance(record, dict)
        or request_id != record.get(
            "gateway_request_id" if safe_gateway_request_id(request_id) else "request_id"
        )
    ):
        raise TransportError("Router returned a mismatched request record.", code="router_response_invalid")
    current = None
    current_status = "unavailable"
    try:
        status = _fetch(base, "/v1/router/status", token, timeout, opener)
        if status.get("object") == "router_status":
            digest = status.get("config_sha256")
            current = {
                "package_version": _label(status.get("package_version")),
                "config_sha256": digest if isinstance(digest, str) and _HEX.fullmatch(digest) else None,
                "uptime_seconds": _number(status.get("uptime_seconds")),
            }
            current_status = "available"
    except OperatorError:
        pass  # The terminal record remains useful when current status is unavailable.
    return {
        "schema": "anvil-router-diagnosis/v1",
        "scope": trace["scope"],
        "request": diagnose_record(record),
        "current_router": {"status": current_status, "metadata": current},
        "limitations": [
            "terminal_records_only", "buffer_eviction_and_restart_can_remove_records",
            "current_router_metadata_is_not_request_time_identity",
            "first_content_is_not_universal_ttft", "timing_does_not_identify_engine_root_cause",
        ],
    }


def dispatch(argv=None) -> CommandResult:
    parser = argparse.ArgumentParser(prog="anvil-serving router diagnose")
    identifier = parser.add_mutually_exclusive_group()
    identifier.add_argument("--request-id")
    identifier.add_argument("--session-id")
    parser.add_argument("--active", action="store_true", help="Read the current active-session snapshot.")
    parser.add_argument("--config", help="Optional router-diagnostics.toml path.")
    parser.add_argument("--router-url")
    parser.add_argument("--auth-env")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args(argv)
    try:
        if args.request_id is None and args.session_id is None and not args.active:
            raise UsageError("Use --request-id, --session-id, or --active.")
        if args.active and args.request_id is not None:
            raise UsageError("--active cannot be combined with --request-id.")
        router_url, token, timeout = _dispatch_settings(args)
        data = (
            diagnose_session(args.session_id, router_url=router_url,
                             token=token, active=args.active, timeout=timeout)
            if args.session_id is not None or args.active else diagnose_request(
                args.request_id, router_url=router_url,
                token=token, timeout=timeout,
            )
        )
    except OperatorError as exc:
        return CommandResult(error=exc, human_stderr=f"router diagnose: {exc.message}\n")
    if args.session_id is not None or args.active:
        label = data["session_id"] or "active requests"
        lines = [f"Session {label}: {len(data['requests'])} records"]
        lines.extend(
            (
                f"Request {item['gateway_request_id'] or item['request_id'] or 'unknown'}: "
                f"{item['active']['phase'] or item['outcome']} "
                f"elapsed_ms={item['active']['elapsed_ms'] if item['active']['elapsed_ms'] is not None else 'unknown'} "
                f"last_activity_ms={item['active']['last_activity_ms'] if item['active']['last_activity_ms'] is not None else 'unknown'}"
            )
            for item in data["requests"]
        )
        if data["truncated"]:
            lines.append("Retained history was truncated by its safety bound.")
        lines.append("No request was replayed.")
        return CommandResult(data=data, human_stdout="\n".join(lines) + "\n")
    request = data["request"]
    lines = [f"Request {request['gateway_request_id'] or request['request_id']}: {request['outcome']}",
             f"Route: {request['route'] or 'unknown'}; tier: {request['requested_tier'] or 'unknown'}"]
    for key, value in request["timing"].items():
        lines.append(f"{key}: {value if value is not None else 'unknown'}")
    lines.append(f"Finish reason: {request['finish_reason'] or 'unknown'}")
    for name in ("prompt", "completion"):
        count = request["usage"][f"{name}_tokens"]
        source = request["usage"][f"{name}_source"]
        lines.append(f"{name.capitalize()} tokens: {count if count is not None else 'unknown'} ({source})")
    if request["output_limit"]["clamped"]:
        lines.append("The router clamped the requested output limit.")
    lines.extend(f"Next check: {check.replace('_', ' ')}" for check in request["next_checks"])
    lines.append("Current router metadata is separate from request-time evidence. No request was replayed.")
    return CommandResult(data=data, human_stdout="\n".join(lines) + "\n")


def main(argv=None) -> int:
    result = dispatch(argv)
    sys.stdout.write(result.human_stdout or "")
    sys.stderr.write(result.human_stderr or "")
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
