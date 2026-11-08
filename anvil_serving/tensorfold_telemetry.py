"""Bounded TensorFold telemetry v1; absent evidence never becomes zero."""
from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation

SCHEMA = "anvil-serving.tensorfold-telemetry/v1"
PREFIX = "tensorfold:"
HISTOGRAMS = ("arrival_to_admission", "admission_to_prefill", "prefill_to_first_token")
BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, math.inf)
CAPABILITIES = {
    "streams": "active-streams", "kv": "global-kv-allocation",
    "timing": "engine-admission-timing", "full_context_bound": "full-context-concurrency",
}
_LINE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)\s*\Z')
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"(?:,|$)')


class _Unavailable(ValueError):
    pass


def _series(raw):
    if not isinstance(raw, str) or len(raw) > 256 * 1024 or len(raw.encode("utf-8")) > 256 * 1024:
        raise ValueError("telemetry must be text bounded to 256 KiB")
    result = {}
    for line in raw.splitlines():
        if not line.startswith((PREFIX + "anvil_", PREFIX + "kv_cache_usage_", PREFIX + "requests_running")):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            raise ValueError("malformed telemetry series")
        name, label_text, value_text = match.groups()
        labels, offset = {}, 0
        for label in _LABEL.finditer(label_text or ""):
            if label.start() != offset or label[1] in labels:
                raise ValueError("malformed or duplicate telemetry label")
            labels[label[1]] = json.loads('"' + label[2] + '"')
            offset = label.end()
        if offset != len(label_text or ""):
            raise ValueError("malformed telemetry labels")
        try:
            value = Decimal(value_text)
        except InvalidOperation as exc:
            raise ValueError("invalid telemetry number") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError("telemetry values must be finite and nonnegative")
        key = tuple(sorted(labels.items()))
        entries = result.setdefault(name.removeprefix(PREFIX), {})
        if key in entries:
            raise ValueError("duplicate telemetry series")
        entries[key] = value
    return result


def _value(series, name, labels=None, *, integer=True):
    entries = series.get(name, {})
    if not entries:
        raise _Unavailable("missing_" + name)
    expected = tuple(sorted((labels or {}).items()))
    if set(entries) != {expected}:
        raise ValueError("ambiguous telemetry series: " + name)
    value = entries[expected]
    if integer:
        if value != int(value) or value > 2**53:
            raise ValueError("telemetry integer out of range: " + name)
        return int(value)
    return float(value)


def _flag(series, name):
    value = _value(series, name)
    if value not in (0, 1):
        raise ValueError("telemetry availability must be zero or one")
    return bool(value)


def _stream_values(series, name, label_name, observed, *, integer):
    entries = series.get(name, {})
    expected = {((label_name, str(index)),) for index in range(observed)}
    if set(entries) != expected:
        raise _Unavailable("missing_or_partial_" + name)
    values = {str(index): entries[((label_name, str(index)),)] for index in range(observed)}
    if integer and any(value != int(value) or value > 2**53 for value in values.values()):
        raise ValueError("stream context tokens must be bounded integers")
    return {key: int(value) if integer else float(value) for key, value in values.items()}


def _streams(series):
    active = _value(series, "anvil_streams_active")
    observed = _value(series, "anvil_streams_observed")
    complete = _flag(series, "anvil_streams_complete")
    if observed > active or observed > 4096:
        raise ValueError("invalid observed stream count")
    contexts = _stream_values(series, "anvil_stream_context_tokens", "stream", observed, integer=True)
    ratios = _stream_values(series, "kv_cache_usage_ratio", "pool", observed, integer=False)
    aliases = _stream_values(series, "kv_cache_usage_perc", "stream", observed, integer=False)
    if ratios != aliases or any(value > 1 for value in ratios.values()):
        raise ValueError("invalid stream occupancy aliases")
    if complete and (active != observed or active != _value(series, "requests_running")):
        raise ValueError("complete stream snapshot does not reconcile")
    return {"status": "measured" if complete else "unavailable", "complete": complete,
            "reason": None if complete else "incomplete_stream_snapshot", "active": active,
            "observed": observed, "context_tokens": contexts, "usage_ratios": ratios}


def _kv(series):
    if not _flag(series, "anvil_kv_allocation_available"):
        raise _Unavailable("global_decode_kv_unavailable")
    if _value(series, "anvil_kv_pool_info", {"scope": "global_logical_decode_kv_pool"}) != 1:
        raise ValueError("invalid global KV pool scope")
    partitions = _value(series, "anvil_kv_pool_partitions")
    if partitions != 1:
        raise _Unavailable("unsupported_global_kv_pool_partitioning")
    values = {name: _value(series, "anvil_kv_" + name) for name in
              ("allocated_pages", "free_pages", "total_pages", "reserved_pages", "page_size_tokens")}
    if (values["allocated_pages"] + values["free_pages"] != values["total_pages"]
            or values["reserved_pages"] > values["free_pages"] or values["page_size_tokens"] <= 0):
        raise ValueError("inconsistent global decode KV allocation")
    return {"status": "measured", "complete": True, "scope": "global_logical_decode_kv_pool",
            "partitions": partitions, **values}


def _histogram(series, stage):
    name = "anvil_" + stage + "_seconds"
    entries = series.get(name + "_bucket", {})
    buckets = {}
    for labels, value in entries.items():
        if len(labels) != 1 or labels[0][0] != "le":
            raise ValueError("invalid histogram labels")
        try:
            edge = float(labels[0][1])
        except ValueError as exc:
            raise ValueError("invalid histogram bucket boundary") from exc
        if edge in buckets or edge not in BUCKETS or value != int(value) or value > 2**53:
            raise ValueError("invalid histogram bucket")
        buckets[edge] = int(value)
    if set(buckets) != set(BUCKETS):
        raise _Unavailable("missing_histogram_buckets_" + stage)
    count = _value(series, name + "_count")
    total = _value(series, name + "_sum", integer=False)
    counts = [buckets[edge] for edge in BUCKETS]
    if counts != sorted(counts) or counts[-1] != count or count == 0 and total != 0:
        raise ValueError("inconsistent histogram counters")
    return {"unit": "seconds", "count": count, "sum": total,
            "buckets": {"+Inf" if math.isinf(edge) else str(edge): buckets[edge] for edge in BUCKETS}}


def _timing(series):
    values = {stage: _histogram(series, stage) for stage in HISTOGRAMS}
    counts = [values[stage]["count"] for stage in HISTOGRAMS]
    boundaries = {name: _value(series, "anvil_" + name + "_total") for name in
                  ("arrivals", "admissions", "prefill_started", "first_tokens")}
    if (counts != sorted(counts, reverse=True) or list(boundaries.values()) != sorted(boundaries.values(), reverse=True)
            or any(count > boundary for count, boundary in zip(counts, list(boundaries.values())[1:]))):
        raise ValueError("timing boundaries are out of order")
    complete = counts == list(boundaries.values())[1:]
    return {"status": "measured" if complete else "unavailable", "complete": complete,
            "reason": None if complete else "incomplete_timing_boundaries", "origin": "engine_submission",
            "boundaries": boundaries, "histograms": values}


def _bound(series, kv):
    if not _flag(series, "anvil_full_context_bound_available"):
        raise _Unavailable("missing_admission_policy_inputs")
    if kv["status"] != "measured":
        raise _Unavailable("global_decode_kv_unavailable")
    info = {"semantics": "additional_full_context_requests", "policy": "full_context_reservation", "formula_version": "1"}
    if _value(series, "anvil_full_context_bound_info", info) != 1 or _value(series, "anvil_kv_pool_partitions") != 1:
        raise ValueError("unsupported full-context bound policy or pool partitioning")
    inputs = {name: _value(series, "anvil_full_context_" + name) for name in
              ("requested_tokens", "reserve_pages", "per_request_reserve_tokens", "admission_limit", "active_requests")}
    if (inputs["requested_tokens"] <= 0 or inputs["active_requests"] > inputs["admission_limit"]
            or inputs["active_requests"] != _value(series, "requests_running")):
        raise ValueError("invalid full-context bound inputs")
    pages_per_request = (inputs["requested_tokens"] + inputs["per_request_reserve_tokens"] + kv["page_size_tokens"] - 1) // kv["page_size_tokens"]
    usable_pages = max(0, kv["free_pages"] - kv["reserved_pages"] - inputs["reserve_pages"])
    expected = min(inputs["admission_limit"] - inputs["active_requests"], usable_pages // pages_per_request)
    if _value(series, "anvil_full_context_bound") != expected:
        raise ValueError("full-context bound formula mismatch")
    return {"status": "estimated", "complete": True, "method": "derived", "value": expected,
            "unit": "requests", **info, "inputs": inputs}


def parse_metrics(raw: str) -> dict:
    """Parse one sample; malformed/contradictory evidence raises ValueError."""
    series = _series(raw)
    result = {"schema": SCHEMA, "version": None, "capabilities": []}
    if "anvil_contract_info" not in series:
        result.update({section: {"status": "unavailable", "complete": False, "reason": "contract_unavailable"}
                       for section in CAPABILITIES})
        return result
    if _value(series, "anvil_contract_info", {"version": "1"}) != 1:
        raise ValueError("unsupported telemetry contract version")
    result["version"] = 1
    for section, read in (("streams", _streams), ("kv", _kv), ("timing", _timing),
                          ("full_context_bound", lambda values: _bound(values, result["kv"]))):
        try:
            result[section] = read(series)
        except _Unavailable as exc:
            result[section] = {"status": "unavailable", "complete": False, "reason": str(exc)}
        if result[section]["complete"]:
            result["capabilities"].append(CAPABILITIES[section])
    return result
