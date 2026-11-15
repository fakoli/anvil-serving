"""Metadata-only managed server-log evidence for SWE benchmark intervals."""

from __future__ import annotations

import json
import re
from datetime import datetime
from collections.abc import Callable, Mapping
from typing import Any

from ..service_runtime.contracts import ServiceError
from ..service_runtime.operations import execute
from .jobs import BenchmarkJobError


_EVENT_KEYS = ("event", "event_type", "operation", "reason", "code")
_EVENTS = frozenset({
    "admission", "admitted", "refusal", "refused", "timeout", "timed_out",
    "disconnect", "disconnected", "ttft", "done", "cancel_acknowledged", "cancellation_acknowledged",
    "cancelled_acknowledged", "lane_release", "lane_released",
})
_IGNIS_EVENTS = {
    "ignis.request.admitted": "admission",
    "ignis.request.ttft": "ttft",
    "ignis.request.done": "done",
    "ignis.request.refused": "refusal",
    "ignis.request.timeout": "timeout",
    "ignis.request.disconnected": "disconnect",
    "ignis.request.cancel_acknowledged": "cancellation_acknowledged",
    "ignis.request.lane_released": "lane_release",
}
_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_TIMESTAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))\s+")
_RFC3339 = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)\Z")
_MAX_EVENTS = 200
_MAX_LOG_BYTES = 1024 * 1024
_TRUNCATION = frozenset({"unknown", "not_truncated", "metadata_event_limit", "capture_limit"})


def validate_server_log_selector(value: Any) -> dict[str, Any]:
    """Validate the explicit managed service selector retained in a job plan."""
    if not isinstance(value, Mapping) or set(value) not in ({"service", "tail"}, {"recipe_container", "tail"}):
        raise BenchmarkJobError("bad_server_log", "server_log must contain exactly service or recipe_container and tail")
    key = "service" if "service" in value else "recipe_container"
    service, tail = value.get(key), value.get("tail")
    if not isinstance(service, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", service):
        raise BenchmarkJobError("bad_server_log", f"server_log.{key} must be a managed identifier")
    if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 1000:
        raise BenchmarkJobError("bad_server_log", "server_log.tail must be an integer from 1 through 1000")
    return {key: service, "tail": tail}


def _timestamp(line: str, event: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    match = _TIMESTAMP.match(line)
    if match and _valid_timestamp(match.group(1)):
        engine = event.get("timestamp")
        return match.group(1), "docker_prefix", engine if isinstance(engine, str) and _valid_timestamp(engine) else None
    value = event.get("timestamp")
    return (value, "engine_embedded", None) if isinstance(value, str) and _valid_timestamp(value) else (None, None, None)


def _valid_timestamp(value: str) -> bool:
    if _RFC3339.fullmatch(value) is None:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _timestamp_key(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event(line: str) -> Mapping[str, Any] | None:
    body = _TIMESTAMP.sub("", line, count=1)
    try:
        value = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def _project(line: str) -> tuple[dict[str, Any] | None, str | None]:
    event = _event(line)
    if event is None:
        return None, None
    event_type = _IGNIS_EVENTS.get(event.get("event_name")) if isinstance(event.get("event_name"), str) else None
    if event_type is None:
        event_type = next((
            event[key] for key in _EVENT_KEYS
            if isinstance(event.get(key), str) and event[key] in _EVENTS
        ), None)
    timestamp, timestamp_source, engine_timestamp = _timestamp(line, event)
    if event_type is None:
        return None, timestamp
    projected: dict[str, Any] = {"event_type": event_type}
    request_id = event.get("request_id", event.get("requestId"))
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        projected["request_id"] = request_id
    attributes = event.get("attributes")
    if isinstance(attributes, Mapping):
        engine_request_id = attributes.get("request_id")
        if isinstance(engine_request_id, int) and not isinstance(engine_request_id, bool) and 0 <= engine_request_id <= 2**63 - 1:
            projected["engine_request_id"] = engine_request_id
        elif isinstance(engine_request_id, str) and _REQUEST_ID.fullmatch(engine_request_id):
            projected["engine_request_id"] = engine_request_id
        lane = attributes.get("lane")
        if isinstance(lane, int) and not isinstance(lane, bool) and 0 <= lane <= 1_000_000:
            projected["lane"] = lane
    status = event.get("status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        projected["status"] = status
    if timestamp is not None:
        projected["timestamp"] = timestamp
        projected["timestamp_source"] = timestamp_source
    if engine_timestamp is not None:
        projected["engine_timestamp"] = engine_timestamp
    return projected, timestamp


def _runtime(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    result: dict[str, Any] = {}
    for key in ("identity", "container_id"):
        item = source.get(key)
        if isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item):
            result[key] = item
    for key in ("image_id", "image_digest"):
        item = source.get(key)
        if isinstance(item, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", item):
            result[key] = item
    if isinstance(source.get("manager"), str) and source["manager"] in {"docker", "launchd"}:
        result["manager"] = source["manager"]
    if isinstance(source.get("observed_at"), str) and _valid_timestamp(source["observed_at"]):
        result["observed_at"] = source["observed_at"]
    if source.get("runtime_interval_continuity") == "unknown":
        result["runtime_interval_continuity"] = "unknown"
    return result


def collect_server_log_metadata(
    selector: Mapping[str, Any],
    started_at: str,
    ended_at: str,
    *,
    operation: Callable[..., Mapping[str, Any]] = execute,
    recipe_operation: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Collect one bounded, declared-service interval without retaining log text."""
    selected = validate_server_log_selector(selector)
    requested = {"started_at": started_at, "ended_at": ended_at}
    try:
        if "service" in selected:
            response = operation(
                "logs", selected["service"], tail=selected["tail"], since=started_at,
                until=ended_at, timestamps=True, dry_run=True,
            )
        else:
            if recipe_operation is None:
                from ..models import collect_discovered_recipe_container_logs
                recipe_operation = collect_discovered_recipe_container_logs
            response = recipe_operation(
                selected["recipe_container"], tail=selected["tail"], since=started_at,
                until=ended_at, timestamps=True,
            )
    except (ServiceError, ValueError) as exc:
        return {
            "status": "unavailable", "selector": selected, "requested_interval": requested,
            "collection_errors": [{"code": getattr(exc, "code", "managed_log_collection_failed")}],
        }
    except OSError:
        return {
            "status": "unavailable", "selector": selected, "requested_interval": requested,
            "collection_errors": [{"code": "managed_log_collection_failed"}],
        }
    lines = response.get("lines")
    if not isinstance(lines, list):
        return {
            "status": "unavailable", "selector": selected, "requested_interval": requested,
            "collection_errors": [{"code": "malformed_managed_log_response"}],
        }
    events: list[dict[str, Any]] = []
    unknown = 0
    omitted = 0
    bytes_seen = 0
    timestamps: list[str] = []
    for index, line in enumerate(lines):
        if index >= selected["tail"]:
            omitted += len(lines) - index
            break
        if not isinstance(line, str):
            unknown += 1
            continue
        bytes_seen += len(line.encode("utf-8", "replace"))
        if bytes_seen > _MAX_LOG_BYTES:
            omitted += len(lines) - index
            break
        projected, timestamp = _project(line)
        if timestamp is not None:
            timestamps.append(timestamp)
        if projected is None:
            unknown += 1
        elif len(events) < _MAX_EVENTS:
            events.append(projected)
        else:
            unknown += 1
    bounds = response.get("bounds") if isinstance(response.get("bounds"), Mapping) else {}
    truncated = bounds.get("truncation", "unknown")
    truncated = truncated if isinstance(truncated, str) else "unknown"
    truncated = truncated if truncated in _TRUNCATION else "unknown"
    if omitted:
        truncated = "capture_limit"
    if len(events) == _MAX_EVENTS:
        truncated = "metadata_event_limit"
    events.sort(key=lambda event: (
        "timestamp" not in event,
        _timestamp_key(event["timestamp"]) if "timestamp" in event else datetime.max.replace(tzinfo=datetime.now().astimezone().tzinfo),
    ))
    timestamps.sort(key=_timestamp_key)
    return {
        "status": "partial" if truncated in {"metadata_event_limit", "capture_limit"} else "collected",
        "selector": selected,
        "requested_interval": requested,
        "observed_runtime": _runtime(response.get("observed_runtime")),
        "clock_basis": {"source": "server_log_timestamps", "offset": "unknown"},
        "rotation": "unknown",
        "bounds": {
            "line_limit": selected["tail"],
            "byte_limit": min(bounds["capture_byte_limit"], _MAX_LOG_BYTES) if type(bounds.get("capture_byte_limit")) is int and bounds["capture_byte_limit"] > 0 else _MAX_LOG_BYTES,
            "truncation": truncated,
            "returned_lines": len(lines),
            "unknown_lines": unknown,
            "omitted_lines": omitted,
            "metadata_event_limit": _MAX_EVENTS,
        },
        "observed_timestamps": {
            "first": timestamps[0] if timestamps else None,
            "last": timestamps[-1] if timestamps else None,
            "completeness": "not_inferred_from_observed_timestamps",
            "order": "timestamp_ordered; ties and clock synchronization unknown",
        },
        "observed_interval": {
            "first_timestamp": timestamps[0] if timestamps else None,
            "last_timestamp": timestamps[-1] if timestamps else None,
            "completeness": "not_inferred_from_observed_timestamps",
            "order": "timestamp_ordered; ties and clock synchronization unknown",
        },
        "events": events,
        "coverage": "partial" if truncated in {"metadata_event_limit", "capture_limit"} else "unknown",
    }


def server_log_collector(selector: Mapping[str, Any]) -> Callable[[str, str], dict[str, Any]]:
    """Bind an already-validated selector; no endpoint-to-runtime inference occurs."""
    selected = validate_server_log_selector(selector)

    def collect(started_at: str, ended_at: str) -> dict[str, Any]:
        return collect_server_log_metadata(selected, started_at, ended_at)

    return collect
