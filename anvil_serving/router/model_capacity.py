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
from collections import deque
from dataclasses import dataclass, replace
import threading
import time
from typing import Callable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .admission import AdmissionSnapshot, TierAdmission
from .availability import AvailabilityResult, resolve_runtime_tier, safe_check, safe_check_member
from .config import METADATA_UPSTREAM, RouterConfig, Tier, normalize_model_alias
from .replica_scheduler import (
    ReplicaPressure,
    PressureFreshness,
    copy_replica_pressure,
    normalize_replica_pressure,
)

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
_MEMBER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_QUALIFICATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
# JSON consumers must retain exact counters; reject beyond IEEE-754's safe
# integer range instead of publishing rounded or unbounded owner state.
_MAX_ADMISSION_COUNT = (1 << 53) - 1
_MEMBER_REASONS = frozenset({
    "identity_passed", "identity_mismatch", "identity_missing", "identity_malformed",
    "identity_oversized", "health_transport", "identity_transport", "probe_pending",
    "replica_probe_not_configured", "replica_member_unknown",
    "member_readiness_not_configured", "availability_member_check_failed",
})

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


@dataclass
class _PressureEntry:
    tier: Tier
    member_capacity: int
    pressure: ReplicaPressure | None = None
    completed_at: float | None = None
    running_at: float | None = None
    queued: bool = False


class ReplicaPressureCache:
    """Bounded non-blocking vLLM-pressure refresh cache for capacity replicas."""

    def __init__(self, tiers: tuple[Tier, ...], *, metrics_provider=None,
                 monotonic=time.monotonic) -> None:
        if metrics_provider is None:
            metrics_provider = fetch_vllm_metrics
        if type(tiers) is not tuple or not callable(metrics_provider) or not callable(monotonic):
            raise ValueError("invalid replica pressure cache")
        entries: dict[tuple[str, str], _PressureEntry] = {}
        ids: set[str] = set()
        total = 0
        for tier in tiers:
            if type(tier) is not Tier or tier.id in ids or type(tier.id) is not str or not tier.id:
                raise ValueError("invalid replica pressure cache")
            ids.add(tier.id)
            if tier.replica_strategy != "capacity" or not 2 <= len(tier.replicas) <= 16:
                raise ValueError("invalid replica pressure cache")
            members: set[str] = set()
            for member in tier.replicas:
                if (
                    member.id in members or _MEMBER_RE.fullmatch(member.id) is None
                    or type(member.max_concurrency) is not int
                    or not 1 <= member.max_concurrency <= 100_000
                ):
                    raise ValueError("invalid replica pressure cache")
                members.add(member.id)
                total += 1
                if total > 256:
                    raise ValueError("invalid replica pressure cache")
                entries[(tier.id, member.id)] = _PressureEntry(
                    replace(tier, base_url=member.base_url, replicas=()), member.max_concurrency
                )
        self._entries = entries
        self._provider = metrics_provider
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._queue: deque[tuple[str, str]] = deque()
        self._workers: list[threading.Thread] = []
        self._closed = False

    def _now(self) -> float | None:
        try:
            value = self._monotonic()
        except Exception:
            return None
        return value if type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

    def _start_workers_locked(self) -> None:
        while len(self._workers) < 2 and len(self._workers) < len(self._queue):
            worker = threading.Thread(target=self._worker, daemon=True)
            self._workers.append(worker)
            worker.start()

    def _schedule_locked(self, key: tuple[str, str], entry: _PressureEntry, now: float | None) -> None:
        if self._closed or entry.queued or entry.running_at is not None or now is None:
            return
        if entry.completed_at is None or now - entry.completed_at >= 1:
            entry.queued = True
            self._queue.append(key)
            self._start_workers_locked()
            self._condition.notify()

    def snapshot(self, tier_id: str) -> dict[str, ReplicaPressure]:
        if type(tier_id) is not str:
            raise ValueError("invalid replica pressure cache query")
        now = self._now()
        with self._condition:
            keys = [key for key in self._entries if key[0] == tier_id]
            if not keys:
                raise ValueError("invalid replica pressure cache query")
            result = {}
            for key in keys:
                entry = self._entries[key]
                if self._closed:
                    result[key[1]] = ReplicaPressure()
                    continue
                self._schedule_locked(key, entry, now)
                pressure = entry.pressure or ReplicaPressure()
                if (
                    pressure.freshness is PressureFreshness.FRESH
                    and entry.completed_at is not None
                    and now is not None
                    and now - entry.completed_at > 5
                ):
                    pressure = ReplicaPressure(
                        PressureFreshness.STALE,
                        None,
                        pressure.requests_state,
                        pressure.kv_state,
                    )
                if entry.running_at is not None and now is not None and now - entry.running_at > 1:
                    pressure = ReplicaPressure(PressureFreshness.FAILED)
                result[key[1]] = copy_replica_pressure(pressure)
            return dict(result)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while not self._closed and not self._queue:
                    self._condition.wait()
                if self._closed:
                    return
                key = self._queue.popleft()
                entry = self._entries.get(key)
                if entry is None or not entry.queued:
                    continue
                entry.queued = False
                started = self._now()
                entry.running_at = started
                tier = entry.tier
            pressure = self._refresh(tier, entry.member_capacity, started)
            ended = self._now()
            with self._condition:
                entry = self._entries.get(key)
                if entry is None or self._closed:
                    continue
                entry.running_at = None
                entry.completed_at = ended
                entry.pressure = pressure

    def _refresh(self, tier: Tier, member_capacity: int, started: float | None) -> ReplicaPressure:
        try:
            snapshot = self._provider(tier)
            ended = self._now()
            if started is None or ended is None or ended - started > 1:
                return ReplicaPressure(PressureFreshness.FAILED)
            if type(snapshot) is not MetricsSnapshot:
                return ReplicaPressure()
            if snapshot.status == "available":
                values = dict(snapshot.values)
                return normalize_replica_pressure(
                    observed_at=ended, now_monotonic=ended, successful=True,
                    requests_running=values.get("requests_running"),
                    requests_waiting=values.get("requests_waiting"),
                    scheduler_capacity=values.get("scheduler_capacity", member_capacity),
                    kv_cache_usage_fraction=values.get("kv_cache_usage_fraction"),
                )
            if snapshot.reason in {"metrics_transport", "metrics_http"}:
                return ReplicaPressure(PressureFreshness.FAILED)
            return ReplicaPressure()
        except Exception:
            return ReplicaPressure(PressureFreshness.FAILED)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            self._condition.notify_all()


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


def replica_metadata(tier: Tier, availability: object) -> tuple[dict, AvailabilityResult]:
    """Project configured members, not endpoints or an attestation of deployment.

    This sibling of the direct projection deliberately never adopts arbitrary
    upstream metadata. A member's observed model is visible only on exact match.
    The returned readiness is the logical OR of verified member readiness; it
    says nothing about admission or qualified aggregate throughput.
    """
    if type(tier.replicas) is not tuple or not 2 <= len(tier.replicas) <= 16:
        raise ValueError("invalid replica projection configuration")
    members = []
    seen = set()
    for member in tier.replicas:
        if (
            type(member.id) is not str or _MEMBER_RE.fullmatch(member.id) is None
            or member.id in seen or type(member.qualification_ref) is not str
            or _QUALIFICATION_RE.fullmatch(member.qualification_ref) is None
        ):
            raise ValueError("invalid replica projection configuration")
        seen.add(member.id)
    identity = tier.replica_identity
    identity_values = {}
    for key, pattern in (
        ("model_revision", _IDENTITY_RE), ("engine_version", _IDENTITY_RE),
        ("image_digest", _DIGEST_RE), ("config_fingerprint", _DIGEST_RE),
    ):
        value = getattr(identity, key, None)
        if type(value) is not str or pattern.fullmatch(value) is None:
            raise ValueError("invalid replica projection configuration")
        identity_values[key] = value
    for member in sorted(tier.replicas, key=lambda item: item.id):
        result = safe_check_member(availability, tier, member.id, include_exception_name=False)
        matched = (
            type(result.expected_model) is str and type(result.observed_model) is str
            and result.expected_model == tier.model and result.observed_model == tier.model
        )
        reason = result.reason
        if type(reason) is not str or (
            reason not in _MEMBER_REASONS
            and re.fullmatch(r"(?:health|identity)_http_(?:[1-5][0-9]{2}|unknown)", reason) is None
        ):
            reason = "unavailable"
        loaded = (
            result.available is True and type(result.state) is str
            and result.state == "ready" and reason == "identity_passed" and matched
        )
        if result.available is True and not matched:
            reason = "identity_mismatch"
        elif not loaded and reason == "identity_passed":
            reason = "unavailable"
        members.append({
            "id": member.id,
            "qualification_ref": member.qualification_ref,
            "readiness": {"loaded": loaded, "state": "ready" if loaded else "unavailable", "reason": reason},
            "served_identity": {"expected": tier.model, "observed": tier.model if matched else None},
        })
    count = sum(row["readiness"]["loaded"] for row in members)
    readiness = AvailabilityResult(
        count > 0, "ready" if count else "unavailable",
        "replicas_ready" if count == len(members) else "replicas_partial" if count else "replicas_unavailable",
        expected_model=tier.model, observed_model=tier.model if count else None,
    )
    return {
        "deployment_identity_source": "declared",
        "runtime_deployment_identity_verified": False,
        "replica_identity": identity_values,
        "members": members,
    }, readiness


def _replica_admission(tier: Tier, admission: Optional[TierAdmission]) -> dict:
    """Read one atomic owner snapshot; an absent/broken owner is not zero load."""
    unavailable = {"status": "unavailable", "state": None, "active_requests": None,
                   "draining": None, "member_active_requests": None}
    if admission is None:
        return unavailable
    try:
        snapshot = admission.snapshot(tier.id)
        if (
            type(snapshot) is not AdmissionSnapshot or snapshot.tier_id != tier.id
            or type(snapshot.state) is not str or snapshot.state not in {"admitting", "quiesced"}
            or type(snapshot.draining) is not bool or type(snapshot.active_requests) is not int
            or (snapshot.draining and snapshot.state != "quiesced")
            or not 0 <= snapshot.active_requests <= _MAX_ADMISSION_COUNT
            or type(snapshot.member_active_requests) is not tuple
            or len(snapshot.member_active_requests) != len(tier.replicas)
        ):
            return unavailable
        counts = {}
        for pair in snapshot.member_active_requests:
            if type(pair) is not tuple or len(pair) != 2:
                return unavailable
            member, count = pair
            if (
                type(member) is not str or member in counts or type(count) is not int
                or not 0 <= count <= _MAX_ADMISSION_COUNT
            ):
                return unavailable
            counts[member] = count
        if set(counts) != {member.id for member in tier.replicas} or sum(counts.values()) != snapshot.active_requests:
            return unavailable
        return {"status": "available", "state": snapshot.state, "active_requests": snapshot.active_requests,
                "draining": snapshot.draining, "member_active_requests": dict(sorted(counts.items()))}
    except Exception:  # noqa: BLE001 - never serialize owner errors or its free-text reason
        return unavailable


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
    *,
    admission: Optional[TierAdmission] = None,
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
        replica, ready = (
            replica_metadata(tier, availability) if tier.replicas else ({}, _readiness(availability, tier))
        )
        if replica:
            # Shared declared per-request policy is not aggregate KV capacity.
            kv_capacity = None
        effective = resolve_runtime_tier(tier, ready)
        reported = effective or tier
        context_limit = (
            reported.context_limit if reported.context_limit > 0 else None
        )
        live = (
            MetricsSnapshot("unavailable", {}, "replica_metrics_not_aggregated")
            if replica else _metrics(metrics_provider, reported)
        )
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

        row = {
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
        }
        if replica:
            row.update(replica)
            row["admission"] = _replica_admission(tier, admission)
            row["capacity"]["scheduler_max_num_seqs"] = None
            row["capacity"]["model_memory_gib"] = None
            row["gpu"]["name"] = None
            row["gpu"]["memory_total_mib"] = None
        rows.append(row)
    return {"object": "list", "data": rows}


__all__ = [
    "MetricsSnapshot",
    "build_model_capacity",
    "fetch_vllm_metrics",
    "replica_metadata",
]
