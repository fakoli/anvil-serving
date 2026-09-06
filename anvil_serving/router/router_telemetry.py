"""Safe, bounded operational views over the router decision-log snapshot.

This module deliberately has no server, clock, or persistence dependency.  A
``DecisionLog`` is an in-memory ring buffer. Its optional record timestamps do
not make this retained snapshot a historical time window, so these functions
must not be presented as time-window statistics or monotonic counters.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

from .decision_log import safe_correlation, safe_gateway_request_id, summarize_decisions

DEFAULT_STATS_LIMIT = 100
MAX_STATS_LIMIT = 10_000
_SCOPE = "current_decision_log_buffer"
_MAX_MEASUREMENT = 1_000_000_000_000_000
_FINISH_REASONS = ("stop", "length", "tool_calls", "content_filter", "unknown")
_REPLICA_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_PRESSURE_FRESHNESS = ("fresh", "stale", "failed", "unknown")
_PRESSURE_SIGNAL = {"valid", "missing", "invalid"}
_MAX_REPLICA_PRESSURE_PPM = 2_000_000_000_000_000
_MAX_REPLICA_MEMBERS = 256


def _query_values(query: Mapping[str, Sequence[str]], name: str) -> list[str]:
    values = query[name]
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]


def _parse_query(query: Optional[Mapping[str, Sequence[str]]]) -> tuple[Optional[str], int]:
    if query is None:
        return None, DEFAULT_STATS_LIMIT
    supported = {"model", "limit"}
    if set(query) - supported:
        raise ValueError("unsupported query parameter")

    model: Optional[str] = None
    if "model" in query:
        values = _query_values(query, "model")
        if len(values) != 1:
            raise ValueError("model must be specified once")
        candidate = values[0].strip().lower()
        if safe_correlation(candidate) is None:
            raise ValueError("invalid model")
        model = candidate

    limit = DEFAULT_STATS_LIMIT
    if "limit" in query:
        values = _query_values(query, "limit")
        if len(values) != 1:
            raise ValueError("limit must be specified once")
        try:
            limit = int(values[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        if not 0 < limit <= MAX_STATS_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_STATS_LIMIT}")
    return model, limit


def _record_value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _route_matches(record: Any, model: Optional[str]) -> bool:
    if model is None:
        return True
    route = _record_value(record, "route")
    return isinstance(route, str) and route.strip().lower() == model


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _percentile(values: Iterable[int], percentile: float) -> Optional[int]:
    """Return an exact observed nearest-rank percentile, or ``None`` without samples."""
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return None
    index = max(math.ceil(percentile * len(ordered)) - 1, 0)
    return ordered[index]


def _nullable_duration(value: Any) -> Optional[int]:
    """Keep unknown phase measurements absent instead of converting them to zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= _MAX_MEASUREMENT else None


def _duration_summary(values: list[int]) -> dict[str, Any]:
    """Summarize observed nonnegative durations; zero is a real observation."""
    if not values:
        return {"samples": 0, "average": None, "p50": None, "p95": None}
    ordered = sorted(values)
    def percentile(percent: float) -> int:
        return ordered[max(math.ceil(percent * len(ordered)) - 1, 0)]
    return {
        "samples": len(ordered),
        "average": sum(ordered) / len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
    }


def _empty_aggregate() -> dict[str, Any]:
    return {
        "requests": {"total": 0, "succeeded": 0, "failed": 0},
        "tokens": {"prompt": 0, "completion": 0, "total": 0},
        "usage_sources": {
            field: {source: 0 for source in ("upstream", "estimated", "unknown")}
            for field in ("prompt", "completion")
        },
        "bytes": {"request": 0, "response": 0, "total": 0},
        "latency_ms": {"samples": 0, "average": None, "p50": None, "p95": None},
        "finish_reason_counts": {reason: 0 for reason in _FINISH_REASONS},
        "_latencies": [],
        "_readiness_checks": [],
        "_upstream_durations": [],
        "_first_content": [],
    }


def _add_record(aggregate: dict[str, Any], record: Mapping[str, Any]) -> None:
    requests = aggregate["requests"]
    requests["total"] += 1
    if record["served_tier"] != "-":
        requests["succeeded"] += 1
    else:
        requests["failed"] += 1

    prompt = _int(record["total_prompt_tokens"])
    completion = _int(record["total_completion_tokens"])
    aggregate["tokens"]["prompt"] += prompt
    aggregate["tokens"]["completion"] += completion
    aggregate["tokens"]["total"] += prompt + completion
    usage = record.get("usage")
    for field in ("prompt", "completion"):
        source = usage.get(f"{field}_source") if isinstance(usage, Mapping) else None
        if source not in ("upstream", "estimated"):
            source = "unknown"
        aggregate["usage_sources"][field][source] += 1

    request_bytes = _int(record["request_bytes"])
    response_bytes = _int(record["response_bytes"])
    aggregate["bytes"]["request"] += request_bytes
    aggregate["bytes"]["response"] += response_bytes
    aggregate["bytes"]["total"] += request_bytes + response_bytes

    latency = _int(record["latency_ms"])
    if latency:
        aggregate["_latencies"].append(latency)
    measurements = record.get("measurements", {})
    if not isinstance(measurements, Mapping):
        measurements = {}
    finish_reason = measurements.get("finish_reason")
    if not isinstance(finish_reason, str) or finish_reason not in _FINISH_REASONS:
        finish_reason = "unknown"
    aggregate["finish_reason_counts"][finish_reason] += 1
    for field, bucket in (
        ("readiness_check_ms", "_readiness_checks"),
        ("upstream_duration_ms", "_upstream_durations"),
        ("time_to_first_content_ms", "_first_content"),
    ):
        observed = _nullable_duration(measurements.get(field))
        if observed is not None:
            aggregate[bucket].append(observed)


def _finish_aggregate(aggregate: dict[str, Any]) -> dict[str, Any]:
    latencies = aggregate.pop("_latencies")
    latency = aggregate["latency_ms"]
    latency["samples"] = len(latencies)
    if latencies:
        latency["average"] = sum(latencies) / len(latencies)
        latency["p50"] = _percentile(latencies, 0.50)
        latency["p95"] = _percentile(latencies, 0.95)
    aggregate["readiness_check_ms"] = _duration_summary(
        aggregate.pop("_readiness_checks")
    )
    aggregate["upstream_duration_ms"] = _duration_summary(
        aggregate.pop("_upstream_durations")
    )
    aggregate["time_to_first_content_ms"] = _duration_summary(
        aggregate.pop("_first_content")
    )
    return aggregate


def aggregate_stats(
    records: Iterable[Any], query: Optional[Mapping[str, Sequence[str]]] = None,
) -> dict[str, Any]:
    """Build safe aggregate statistics for a bounded decision-log snapshot.

    ``model`` filters the public route alias and ``limit`` retains the most
    recent matching records. The returned ``scope`` is the current buffer, not a
    requested time range even when retained records carry creation timestamps.
    """
    model, limit = _parse_query(query)
    all_records = list(records)
    matching = [record for record in all_records if _route_matches(record, model)]
    selected = matching[-limit:]
    projection = summarize_decisions(selected, limit=max(len(selected), 1))["records"]

    totals = _empty_aggregate()
    routes: dict[str, dict[str, Any]] = {}
    for record in projection:
        _add_record(totals, record)
        route = record["route"]
        route_stats = routes.setdefault(route, _empty_aggregate())
        _add_record(route_stats, record)

    return {
        "object": "router_stats",
        "scope": _SCOPE,
        "available": len(all_records),
        "matching": len(matching),
        "count": len(projection),
        "limit": limit,
        "model": model,
        **_finish_aggregate(totals),
        "routes": {
            route: _finish_aggregate(route_stats)
            for route, route_stats in sorted(routes.items())
        },
    }


def find_request(records: Iterable[Any], request_id: str) -> dict[str, Any]:
    """Return the newest safe metadata projection for one correlation identifier.

    ``KeyError`` deliberately represents an invalid or absent identifier so a
    front door can return its normal not-found response without reflecting an
    unsafe caller-supplied value.
    """
    candidate = safe_correlation(request_id)
    if candidate is None:
        raise KeyError(request_id)
    snapshot = list(records)
    # The gateway-generated id is authoritative. Search it before accepting a
    # legacy caller id so an inbound correlation value cannot shadow a record.
    gateway_id = safe_gateway_request_id(candidate)
    if gateway_id is not None:
        for record in reversed(snapshot):
            if safe_gateway_request_id(_record_value(record, "gateway_request_id")) == gateway_id:
                return {
                    "object": "router_request",
                    "scope": _SCOPE,
                    "record": summarize_decisions((record,), limit=1)["records"][0],
                }
        # Reserve the generated namespace: a caller cannot impersonate an
        # absent/evicted gateway record by supplying its id as legacy lineage.
        raise KeyError(candidate)
    for record in reversed(snapshot):
        if safe_correlation(_record_value(record, "request_id")) == candidate:
            return {
                "object": "router_request",
                "scope": _SCOPE,
                "record": summarize_decisions((record,), limit=1)["records"][0],
            }
    raise KeyError(candidate)


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric_lines(name: str, help_text: str, rows: list[tuple[str, Any]]) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    for model, value in rows:
        rendered = 0 if value is None else value
        lines.append(f'{name}{{model="{_prometheus_label(model)}"}} {rendered}')
    return lines


def _request_metric_lines(rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    name = "anvil_router_decision_buffer_requests"
    lines = [
        f"# HELP {name} Current buffer request count by outcome",
        f"# TYPE {name} gauge",
    ]
    for model, aggregate in rows:
        for outcome, value in aggregate["requests"].items():
            lines.append(
                f'{name}{{model="{_prometheus_label(model)}",outcome="{outcome}"}} {value}'
            )
    return lines


def render_prometheus(
    records: Iterable[Any], query: Optional[Mapping[str, Sequence[str]]] = None,
) -> str:
    """Render current decision-buffer aggregates in Prometheus text format.

    Every metric is a gauge: ring-buffer eviction and router restarts can reduce
    the values, so monotonic counter semantics would be false.
    """
    stats = aggregate_stats(records, query)
    rows = [("", stats)] + list(stats["routes"].items())
    metrics = (
        ("anvil_router_decision_buffer_prompt_tokens", "Current buffer prompt tokens", "tokens.prompt"),
        ("anvil_router_decision_buffer_completion_tokens", "Current buffer completion tokens", "tokens.completion"),
        ("anvil_router_decision_buffer_request_bytes", "Current buffer request bytes", "bytes.request"),
        ("anvil_router_decision_buffer_response_bytes", "Current buffer response bytes", "bytes.response"),
        ("anvil_router_decision_buffer_latency_ms_average", "Current buffer average positive latency milliseconds", "latency_ms.average"),
        ("anvil_router_decision_buffer_latency_ms_p50", "Current buffer exact p50 positive latency milliseconds", "latency_ms.p50"),
        ("anvil_router_decision_buffer_latency_ms_p95", "Current buffer exact p95 positive latency milliseconds", "latency_ms.p95"),
        ("anvil_router_decision_buffer_latency_samples", "Current buffer positive latency sample count", "latency_ms.samples"),
        ("anvil_router_decision_buffer_readiness_check_ms_average", "Current buffer average readiness check milliseconds", "readiness_check_ms.average"),
        ("anvil_router_decision_buffer_readiness_check_ms_p50", "Current buffer exact p50 readiness check milliseconds", "readiness_check_ms.p50"),
        ("anvil_router_decision_buffer_readiness_check_ms_p95", "Current buffer exact p95 readiness check milliseconds", "readiness_check_ms.p95"),
        ("anvil_router_decision_buffer_readiness_check_samples", "Current buffer readiness check sample count", "readiness_check_ms.samples"),
        ("anvil_router_decision_buffer_upstream_duration_ms_average", "Current buffer average upstream duration milliseconds", "upstream_duration_ms.average"),
        ("anvil_router_decision_buffer_upstream_duration_ms_p50", "Current buffer exact p50 upstream duration milliseconds", "upstream_duration_ms.p50"),
        ("anvil_router_decision_buffer_upstream_duration_ms_p95", "Current buffer exact p95 upstream duration milliseconds", "upstream_duration_ms.p95"),
        ("anvil_router_decision_buffer_upstream_duration_samples", "Current buffer upstream duration sample count", "upstream_duration_ms.samples"),
        ("anvil_router_decision_buffer_time_to_first_content_ms_average", "Current buffer average first content delta milliseconds", "time_to_first_content_ms.average"),
        ("anvil_router_decision_buffer_time_to_first_content_ms_p50", "Current buffer exact p50 first content delta milliseconds", "time_to_first_content_ms.p50"),
        ("anvil_router_decision_buffer_time_to_first_content_ms_p95", "Current buffer exact p95 first content delta milliseconds", "time_to_first_content_ms.p95"),
        ("anvil_router_decision_buffer_time_to_first_content_samples", "Current buffer first content delta sample count", "time_to_first_content_ms.samples"),
    )
    lines = _request_metric_lines(rows)
    for name, help_text, path in metrics:
        keys = path.split(".")
        values = []
        for model, aggregate in rows:
            value: Any = aggregate
            for key in keys:
                value = value[key]
            values.append((model, value))
        lines.extend(_metric_lines(name, help_text, values))
    return "\n".join(lines) + "\n"


def render_process_prometheus(started_at: float, buffer_capacity: Optional[int]) -> str:
    """Render restart-detectability gauges (ADR-0033).

    ``anvil_router_process_start_time_seconds`` lets scrapers mask or annotate
    the resets every buffer gauge legitimately undergoes on restart, and the
    buffer capacity exposes when eviction saturation makes the snapshot a
    trailing window rather than a session total.
    """
    lines = [
        "# HELP anvil_router_process_start_time_seconds Unix time the router process started.",
        "# TYPE anvil_router_process_start_time_seconds gauge",
        "anvil_router_process_start_time_seconds %.3f" % float(started_at),
    ]
    if buffer_capacity is not None:
        lines += [
            "# HELP anvil_router_decision_buffer_capacity Maximum records the decision ring buffer retains.",
            "# TYPE anvil_router_decision_buffer_capacity gauge",
            "anvil_router_decision_buffer_capacity %d" % buffer_capacity,
        ]
    return "\n".join(lines) + "\n"


def _bounded_int(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _capacity_replica_group(row: object):
    """Return one strict capacity group or None; never coerce hostile values."""
    if type(row) is not dict:
        return None
    tier_id = row.get("id")
    admission = row.get("admission")
    metadata_members = row.get("members")
    if (
        type(tier_id) is not str
        or _REPLICA_ID.fullmatch(tier_id) is None
        or type(admission) is not dict
        or set(admission) != {
            "status", "state", "active_requests", "draining",
            "member_active_requests", "max_concurrency", "members",
        }
        or type(admission.get("status")) is not str
        or admission.get("status") != "available"
        or type(admission.get("state")) is not str
        or admission.get("state") not in {"admitting", "quiesced"}
        or type(admission.get("draining")) is not bool
        or type(admission.get("members")) is not list
        or type(metadata_members) is not list
        or not 2 <= len(admission["members"]) <= 16
        or len(metadata_members) != len(admission["members"])
        or not _bounded_int(admission.get("max_concurrency"), 1, (1 << 53) - 1)
        or not _bounded_int(admission.get("active_requests"), 0, (1 << 53) - 1)
        or admission["active_requests"] > admission["max_concurrency"]
        or admission["draining"]
        and (admission["state"] != "quiesced" or admission["active_requests"] == 0)
    ):
        return None
    active_map = admission.get("member_active_requests")
    if type(active_map) is not dict or len(active_map) != len(admission["members"]):
        return None
    projected = {}
    for member in admission["members"]:
        if type(member) is not dict or set(member) != {
            "id", "state", "active_requests", "max_concurrency", "draining",
        }:
            return None
        member_id = member.get("id")
        active = member.get("active_requests")
        ceiling = member.get("max_concurrency")
        draining = member.get("draining")
        if (
            type(member_id) is not str
            or _REPLICA_ID.fullmatch(member_id) is None
            or member_id in projected
            or type(member.get("state")) is not str
            or member.get("state") not in {"admitting", "quiesced"}
            or not _bounded_int(active, 0, 100_000)
            or not _bounded_int(ceiling, 1, 100_000)
            or active > ceiling
            or type(draining) is not bool
            or draining and (member["state"] != "quiesced" or active == 0)
            or type(active_map.get(member_id)) is not int
            or active_map[member_id] != active
        ):
            return None
        projected[member_id] = [active, ceiling]
    if set(active_map) != set(projected) or sum(row[0] for row in projected.values()) != admission["active_requests"]:
        return None
    telemetry = {}
    for member in metadata_members:
        if type(member) is not dict:
            return None
        member_id = member.get("id")
        freshness = member.get("freshness")
        pressure = member.get("pressure_ppm")
        if (
            type(member_id) is not str
            or member_id not in projected
            or member_id in telemetry
            or type(freshness) is not str
            or freshness not in _PRESSURE_FRESHNESS
            or type(member.get("requests_state")) is not str
            or member["requests_state"] not in _PRESSURE_SIGNAL
            or type(member.get("kv_state")) is not str
            or member["kv_state"] not in _PRESSURE_SIGNAL
            or freshness == "fresh"
            and not _bounded_int(pressure, 0, _MAX_REPLICA_PRESSURE_PPM)
            or freshness == "fresh"
            and (
                "invalid" in (member["requests_state"], member["kv_state"])
                or "valid" not in (member["requests_state"], member["kv_state"])
            )
            or freshness != "fresh" and pressure is not None
        ):
            return None
        telemetry[member_id] = (freshness, pressure)
    if set(telemetry) != set(projected):
        return None
    return tier_id, admission["active_requests"], admission["max_concurrency"], tuple(
        (member_id, *projected[member_id], *telemetry[member_id])
        for member_id in sorted(projected)
    )


def _replica_capacity_metric_lines(rows: object) -> list[str]:
    if type(rows) is not list:
        return []
    groups = []
    member_count = 0
    tier_ids = set()
    for row in rows:
        group = _capacity_replica_group(row)
        if group is None:
            continue
        if group[0] in tier_ids or member_count + len(group[3]) > _MAX_REPLICA_MEMBERS:
            continue
        groups.append(group)
        tier_ids.add(group[0])
        member_count += len(group[3])
    if not groups:
        return []
    specs = (
        ("anvil_router_replica_tier_active_requests", "Current active requests for a capacity replica tier"),
        ("anvil_router_replica_tier_max_concurrency", "Configured maximum concurrency for a capacity replica tier"),
        ("anvil_router_replica_member_active_requests", "Current active requests for a capacity replica member"),
        ("anvil_router_replica_member_max_concurrency", "Configured maximum concurrency for a capacity replica member"),
        ("anvil_router_replica_member_pressure_ppm", "Current fresh normalized pressure for a capacity replica member"),
        ("anvil_router_replica_member_pressure_freshness", "Current pressure freshness class for a capacity replica member"),
    )
    lines = []
    for name, help_text in specs:
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        for tier_id, tier_active, tier_ceiling, members in groups:
            tier_label = _prometheus_label(tier_id)
            if name == specs[0][0]:
                lines.append(f'{name}{{tier="{tier_label}"}} {tier_active}')
            elif name == specs[1][0]:
                lines.append(f'{name}{{tier="{tier_label}"}} {tier_ceiling}')
            else:
                for member_id, active, ceiling, freshness, pressure in members:
                    labels = f'tier="{tier_label}",member="{_prometheus_label(member_id)}"'
                    if name == specs[2][0]:
                        lines.append(f"{name}{{{labels}}} {active}")
                    elif name == specs[3][0]:
                        lines.append(f"{name}{{{labels}}} {ceiling}")
                    elif name == specs[4][0] and pressure is not None:
                        lines.append(f"{name}{{{labels}}} {pressure}")
                    elif name == specs[5][0]:
                        for state in _PRESSURE_FRESHNESS:
                            lines.append(
                                f'{name}{{{labels},freshness="{state}"}} '
                                f'{1 if freshness == state else 0}'
                            )
    return lines


def render_capacity_prometheus(snapshot: Mapping[str, Any]) -> str:
    """Render safe per-alias model-capacity gauges from a capacity snapshot."""
    specs = (
        ("anvil_router_model_loaded", "Whether the configured model tier is ready", "loaded"),
        ("anvil_router_model_context_limit_tokens", "Configured model context limit", "capacity.context_limit_tokens"),
        ("anvil_router_model_kv_cache_capacity_tokens", "Measured model KV cache token capacity", "capacity.kv_cache_capacity_tokens"),
        ("anvil_router_model_image_limit", "Configured images per request", "multimodal.image_limit"),
        ("anvil_router_model_requests_running", "Live engine running requests", "live.requests_running"),
        ("anvil_router_model_requests_waiting", "Live engine waiting requests", "live.requests_waiting"),
        ("anvil_router_model_kv_cache_usage_fraction", "Live engine KV cache usage fraction", "live.kv_cache_usage_fraction"),
    )
    rows = snapshot.get("data", ())
    lines: list[str] = []
    for name, help_text, _path in specs:
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, Mapping):
                continue
            tier_id = str(row.get("id") or "")
            aliases = row.get("aliases")
            if not isinstance(aliases, list):
                continue
            value: Any = row
            for key in _path.split("."):
                value = value.get(key) if isinstance(value, Mapping) else None
            if isinstance(value, bool):
                value = int(value)
            if not isinstance(value, (int, float)):
                continue
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                lines.append(
                    f'{name}{{model="{_prometheus_label(alias)}",'
                    f'tier="{_prometheus_label(tier_id)}"}} {value}'
                )
    lines.extend(_replica_capacity_metric_lines(rows))
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "DEFAULT_STATS_LIMIT",
    "MAX_STATS_LIMIT",
    "aggregate_stats",
    "find_request",
    "render_capacity_prometheus",
    "render_prometheus",
]
