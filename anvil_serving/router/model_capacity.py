"""Secret-free loaded-model capacity for ``GET /v1/models/capacity``.

The router owns model aliases and readiness, while the serving engine owns
live scheduler/cache telemetry.  This module joins those two read-only views
without giving the router Docker-socket or GPU-device access.

Static capacity facts are opt-in metadata under ``tier.params.capacity``.
Only the allowlisted fields below are emitted; arbitrary tier params are never
returned.  Live values come from the selected tier's bounded ``/metrics``
surface and degrade to an unavailable status on any transport or parse fault.
"""
from __future__ import annotations

import math
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .availability import AvailabilityResult, resolve_runtime_tier, safe_check
from .config import METADATA_UPSTREAM, RouterConfig, Tier, normalize_model_alias

CAPACITY_PARAMS_KEY = "capacity"
_MAX_METRICS_BYTES = 1024 * 1024
_METRIC_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)"
    r"(?:\s+[-+]?[0-9]+)?$"
)
_MODEL_LABEL_RE = re.compile(r'(?:^|,)model_name="((?:[^"\\]|\\.)*)"')
_ROLE_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")

_LIVE_METRICS = {
    "vllm:num_requests_running": "requests_running",
    "vllm:num_requests_waiting": "requests_waiting",
    "vllm:kv_cache_usage_perc": "kv_cache_usage_fraction",
    "vllm:num_preemptions_total": "preemptions_total",
    "vllm:mm_cache_queries_total": "multimodal_cache_queries_total",
    "vllm:mm_cache_hits_total": "multimodal_cache_hits_total",
}


@dataclass(frozen=True)
class MetricsSnapshot:
    """Bounded, content-free result from one engine metrics read."""

    status: str
    values: Mapping[str, float]
    reason: Optional[str] = None


MetricsProvider = Callable[[Tier], MetricsSnapshot]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep an upstream bearer token on the configured authority."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _metrics_url(tier: Tier) -> str:
    parsed = urlsplit(tier.base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/metrics", "", ""))


def _parse_metrics(payload: bytes, model: Optional[str]) -> Mapping[str, float]:
    text = payload.decode("utf-8")
    selected: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _METRIC_RE.fullmatch(line.strip())
        if match is None or match.group("name") not in _LIVE_METRICS:
            continue
        labels = match.group("labels") or ""
        model_match = _MODEL_LABEL_RE.search(labels)
        if model and model_match and model_match.group(1) != model:
            continue
        value = float(match.group("value"))
        if math.isfinite(value):
            key = _LIVE_METRICS[match.group("name")]
            selected[key] = selected.get(key, 0.0) + value
    return selected


def fetch_vllm_metrics(
    tier: Tier,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Optional[Callable[..., object]] = None,
    timeout: float = 1.0,
    max_bytes: int = _MAX_METRICS_BYTES,
) -> MetricsSnapshot:
    """Read one tier's Prometheus metrics with bounded, direct HTTP transport."""
    environ = os.environ if env is None else env
    headers = {"Accept": "text/plain"}
    token = environ.get(tier.auth_env, "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        _metrics_url(tier), headers=headers, method="GET"
    )
    transport = opener if opener is not None else urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    ).open
    try:
        with transport(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                return MetricsSnapshot("unavailable", {}, "metrics_http")
            payload = response.read(max_bytes + 1)
    except urllib.error.HTTPError:
        return MetricsSnapshot("unavailable", {}, "metrics_http")
    except Exception:  # noqa: BLE001 - raw endpoint/transport details stay private
        return MetricsSnapshot("unavailable", {}, "metrics_transport")
    if len(payload) > max_bytes:
        return MetricsSnapshot("unavailable", {}, "metrics_oversized")
    try:
        values = _parse_metrics(payload, tier.model)
    except (UnicodeDecodeError, ValueError):
        return MetricsSnapshot("unavailable", {}, "metrics_malformed")
    if not values:
        return MetricsSnapshot("unavailable", {}, "metrics_missing")
    return MetricsSnapshot("available", values)


def _positive_int(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonnegative_int(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _positive_number(value) -> Optional[float]:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value <= 0
        or not math.isfinite(value)
    ):
        return None
    return float(value)


def _safe_text(value, *, role: bool = False) -> Optional[str]:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if role and _ROLE_RE.fullmatch(value) is None:
        return None
    return value


def _capacity_params(tier: Tier) -> Mapping[str, object]:
    params = tier.params
    if not isinstance(params, Mapping):
        return {}
    capacity = params.get(CAPACITY_PARAMS_KEY)
    return capacity if isinstance(capacity, Mapping) else {}


def _readiness(availability, tier: Tier) -> AvailabilityResult:
    return safe_check(availability, tier, include_exception_name=False)


def _metrics(provider: MetricsProvider, tier: Tier) -> MetricsSnapshot:
    try:
        result = provider(tier)
        if (
            isinstance(result, MetricsSnapshot)
            and result.status in {"available", "unavailable"}
        ):
            return result
    except Exception:  # noqa: BLE001 - one metrics fault must not fail the snapshot
        pass
    return MetricsSnapshot("unavailable", {}, "metrics_provider")


def _integer_arg(query: Mapping[str, list[str]], name: str) -> Optional[int]:
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not values[0].isdigit():
        raise ValueError(f"{name} must be a non-negative integer")
    value = int(values[0])
    if value > 10_000_000:
        raise ValueError(f"{name} is too large")
    return value


def _scenario(
    query: Mapping[str, list[str]],
    *,
    context_limit: Optional[int],
    image_limit: Optional[int],
    video_limit: Optional[int],
    image_tokens_estimate: Optional[int],
    video_tokens_estimate: Optional[int],
) -> Optional[dict]:
    names = (
        "images", "videos", "input_tokens", "image_tokens", "video_tokens",
        "output_tokens",
    )
    if not any(name in query for name in names):
        return None
    images = _integer_arg(query, "images") or 0
    videos = _integer_arg(query, "videos") or 0
    input_tokens = _integer_arg(query, "input_tokens") or 0
    requested_image_tokens = _integer_arg(query, "image_tokens")
    requested_video_tokens = _integer_arg(query, "video_tokens")
    image_tokens = (
        requested_image_tokens
        if requested_image_tokens is not None
        else images * image_tokens_estimate
        if image_tokens_estimate is not None
        else None
    )
    video_tokens = (
        requested_video_tokens
        if requested_video_tokens is not None
        else videos * video_tokens_estimate
        if video_tokens_estimate is not None
        else None
    )
    output_tokens = _integer_arg(query, "output_tokens") or 0

    within_image_limit = None if image_limit is None else images <= image_limit
    within_video_limit = None if video_limit is None else videos <= video_limit
    context_tokens = None
    within_context = None
    if (
        (images == 0 or image_tokens is not None)
        and (videos == 0 or video_tokens is not None)
    ):
        context_tokens = (
            input_tokens + (image_tokens or 0) + (video_tokens or 0)
            + output_tokens
        )
        within_context = (
            context_tokens <= context_limit
            if context_limit is not None
            else None
        )
    if (
        within_image_limit is False
        or within_video_limit is False
        or within_context is False
    ):
        allowed = False
    elif (
        within_image_limit is True
        and within_video_limit is True
        and within_context is True
    ):
        allowed = True
    else:
        allowed = None
    notes = []
    if images > 0 and image_tokens is None:
        notes.append(
            "image_tokens or a configured image_tokens_estimate is required "
            "when images are present"
        )
    if videos > 0 and video_tokens is None:
        notes.append(
            "video_tokens or a configured video_tokens_estimate is required "
            "when videos are present"
        )
    return {
        "images": images,
        "videos": videos,
        "input_tokens": input_tokens,
        "image_tokens": image_tokens,
        "video_tokens": video_tokens,
        "image_tokens_source": (
            "request"
            if requested_image_tokens is not None
            else "configured_estimate"
            if images > 0 and image_tokens_estimate is not None
            else None
        ),
        "video_tokens_source": (
            "request"
            if requested_video_tokens is not None
            else "configured_estimate"
            if videos > 0 and video_tokens_estimate is not None
            else None
        ),
        "output_tokens": output_tokens,
        "context_tokens": context_tokens,
        "within_image_limit": within_image_limit,
        "within_video_limit": within_video_limit,
        "within_context_limit": within_context,
        "allowed": allowed,
        "note": "; ".join(notes) or None,
    }


def build_model_capacity(
    config: RouterConfig,
    availability,
    metrics_provider: MetricsProvider,
    query: Mapping[str, list[str]],
) -> dict:
    """Build a capacity snapshot for configured chat tiers."""
    supported = {
        "model", "gpu_role", "images", "videos", "input_tokens",
        "image_tokens", "video_tokens", "output_tokens",
    }
    if set(query) - supported:
        raise ValueError("unsupported query parameter")

    selected_alias: Optional[str] = None
    if "model" in query:
        values = query["model"]
        if len(values) != 1:
            raise ValueError("model must be specified once")
        selected_alias = normalize_model_alias(values[0])
        if selected_alias not in config.model_routes:
            raise KeyError(selected_alias)

    selected_role: Optional[str] = None
    if "gpu_role" in query:
        values = query["gpu_role"]
        if len(values) != 1 or _ROLE_RE.fullmatch(values[0]) is None:
            raise ValueError("invalid gpu_role")
        selected_role = values[0]

    aliases_by_tier: dict[str, list[str]] = {}
    for alias, tier_id in config.model_routes.items():
        aliases_by_tier.setdefault(tier_id, []).append(alias)

    rows = []
    for tier in config.tiers:
        aliases = sorted(aliases_by_tier.get(tier.id, ()))
        if selected_alias is not None and selected_alias not in aliases:
            continue
        capacity = _capacity_params(tier)
        gpu_role = _safe_text(capacity.get("gpu_role"), role=True)
        if selected_role is not None and gpu_role != selected_role:
            continue

        image_limit = _nonnegative_int(capacity.get("image_limit"))
        video_limit = _nonnegative_int(capacity.get("video_limit"))
        image_tokens_estimate = _nonnegative_int(
            capacity.get("image_tokens_estimate")
        )
        video_tokens_estimate = _nonnegative_int(
            capacity.get("video_tokens_estimate")
        )
        kv_capacity = _positive_int(capacity.get("kv_cache_capacity_tokens"))
        ready = _readiness(availability, tier)
        effective = resolve_runtime_tier(tier, ready)
        reported = effective or tier
        context_limit = (
            reported.context_limit if reported.context_limit > 0 else None
        )
        live = _metrics(metrics_provider, reported)
        values = dict(live.values) if live.status == "available" else {}
        usage = values.get("kv_cache_usage_fraction")
        used_tokens = None
        remaining_tokens = None
        if (
            kv_capacity is not None
            and isinstance(usage, (int, float))
            and 0.0 <= usage <= 1.0
        ):
            used_tokens = round(kv_capacity * usage)
            remaining_tokens = kv_capacity - used_tokens

        rows.append({
            "object": "model_capacity",
            "id": tier.id,
            "aliases": aliases,
            "model": (
                reported.model
                if tier.metadata_source != METADATA_UPSTREAM or effective is not None
                else None
            ),
            "loaded": ready.available,
            "readiness": {
                "state": ready.state,
                "reason": ready.reason
                if re.fullmatch(r"[A-Za-z0-9_]{1,64}", ready.reason or "")
                else "unavailable",
            },
            "engine": {
                "name": reported.engine,
                "quantization": reported.quantization,
            },
            "gpu": {
                "role": gpu_role,
                "name": _safe_text(capacity.get("gpu_name")),
                "memory_total_mib": _positive_int(
                    capacity.get("gpu_memory_total_mib")
                ),
            },
            "capacity": {
                "context_limit_tokens": context_limit,
                "kv_cache_capacity_tokens": kv_capacity,
                "full_context_concurrency": (
                    round(kv_capacity / context_limit, 3)
                    if kv_capacity is not None and context_limit is not None
                    else None
                ),
                "configured_max_concurrency": tier.max_concurrency,
                "scheduler_max_num_seqs": _positive_int(
                    capacity.get("scheduler_max_num_seqs")
                ),
                "model_memory_gib": _positive_number(
                    capacity.get("model_memory_gib")
                ),
            },
            "multimodal": {
                "admission_enabled": capacity.get("media_admission_enabled") is True,
                "image_limit": image_limit,
                "video_limit": video_limit,
                "image_tokens_estimate": image_tokens_estimate,
                "video_tokens_estimate": video_tokens_estimate,
            },
            "live": {
                "status": live.status,
                "reason": live.reason,
                "requests_running": values.get("requests_running"),
                "requests_waiting": values.get("requests_waiting"),
                "kv_cache_usage_fraction": usage,
                "kv_cache_used_tokens_estimate": used_tokens,
                "kv_cache_remaining_tokens_estimate": remaining_tokens,
                "preemptions_total": values.get("preemptions_total"),
                "multimodal_cache_queries_total": values.get(
                    "multimodal_cache_queries_total"
                ),
                "multimodal_cache_hits_total": values.get(
                    "multimodal_cache_hits_total"
                ),
            },
            "scenario": _scenario(
                query,
                context_limit=context_limit,
                image_limit=image_limit,
                video_limit=video_limit,
                image_tokens_estimate=image_tokens_estimate,
                video_tokens_estimate=video_tokens_estimate,
            ),
        })
    return {"object": "list", "data": rows}


__all__ = [
    "MetricsSnapshot",
    "build_model_capacity",
    "fetch_vllm_metrics",
]
