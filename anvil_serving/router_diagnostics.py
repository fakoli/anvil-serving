"""Read-only, bounded request diagnostics; never replay model traffic."""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

from .operator_output import CommandResult, OperatorError, TransportError, UsageError
from .router.decision_log import safe_correlation, safe_gateway_request_id

MAX_RESPONSE_BYTES = 128 * 1024
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SECRET = re.compile(r"(?i)(?:bearer|sk-|hf_|token|secret|password|api.key)")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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


def diagnose_record(record: Mapping) -> dict:
    """Project known metadata and derive bounded observations, never content."""
    attempts = record.get("attempts")
    attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
    if not isinstance(attempt, Mapping):
        attempt = {}
    reason = attempt.get("reason")
    if not isinstance(reason, str):
        reason = None
    succeeded = attempt.get("succeeded") is True
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
    if not succeeded:
        observations.append("request_failed")
        if reason in {"over_context", "media_admission_context_limit"}:
            checks.append("check_request_context_and_media_limits")
        elif reason in {"quiesced", "unavailable", "upstream_metadata_unavailable", "backend_unbound"}:
            checks.append("check_selected_tier_readiness_and_admission")
        elif reason == "client_disconnected":
            checks.append("check_client_timeout_or_cancellation")
        else:
            checks.append("inspect_selected_upstream_logs_using_request_id")
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
        "requested_tier": _label(record.get("requested_tier")),
        "served_tier": _label(record.get("served_tier")),
        "outcome": "succeeded" if succeeded else "failed" if attempts else "unknown",
        "finish_reason": finish,
        "timing": timing,
        "usage": safe_usage,
        "output_limit": safe_limit,
        "observations": observations,
        "next_checks": checks,
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
        or trace.get("scope") != "current_decision_log_buffer"
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
        "scope": "current_decision_log_buffer",
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
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--router-url", default=os.environ.get("ANVIL_ROUTER_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--auth-env", default="ANVIL_ROUTER_TOKEN")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        data = diagnose_request(
            args.request_id, router_url=args.router_url,
            token=os.environ.get(args.auth_env), timeout=args.timeout,
        )
    except OperatorError as exc:
        return CommandResult(error=exc, human_stderr=f"router diagnose: {exc.message}\n")
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
