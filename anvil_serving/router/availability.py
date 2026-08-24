"""Runtime tier availability for direct local routing.

Router configuration describes which upstreams *may* serve a request.  This
module answers the narrower runtime question: is a configured local upstream
ready right now?  A cached, bounded HTTP health probe keeps a stopped or
starting model container out of the request path without rewriting router TOML
or teaching the router how to operate Docker.

The default implementation is deliberately conservative:

* local tiers without ``health_path`` are treated as available;
* configured probes use the tier's scheme/authority and replace only the path;
* probe failures return structured state and never raise into routing;
* results are cached for ``probe_interval`` seconds to avoid request-time probe
  storms, and a recovered endpoint is automatically readmitted after expiry.

This is readiness, not model-quality evaluation. Quality is established by the
separate benchmark and preflight tools.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Dict, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .config import METADATA_UPSTREAM, PRIVACY_LOCAL, RouterConfig, Tier


_SAFE_METADATA_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+ -]{0,255}")
_CONTEXT_KEYS = ("max_model_len", "max_context_length", "context_length")


@dataclass(frozen=True)
class RuntimeModelMetadata:
    """Allowlisted inference-service facts adopted by an upstream-owned tier."""

    model: str
    context_limit: int
    engine: Optional[str] = None
    quantization: Optional[str] = None
    engine_version: Optional[str] = None
    max_concurrency: Optional[int] = None
    modalities: tuple[str, ...] = ()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _direct_opener():
    """Return the shared token-safe probe transport policy."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    ).open


@dataclass(frozen=True)
class AvailabilityResult:
    """One tier's bounded readiness result.

    ``reason`` is a stable, content-free code suitable for decision metadata.
    Raw exception messages and URLs are intentionally excluded.

    ``latency_ms`` and ``checked_at`` are optional freshness metadata stamped by
    :meth:`HttpHealthAvailability.check` when it actually runs a probe (they stay
    ``None`` for the no-probe / not-configured paths and for
    :class:`AlwaysAvailable`).  They exist so a readiness snapshot can report
    *when* a serve was last checked and *how long* that probe took without
    re-probing on every read.  They are trailing/defaulted so every existing
    positional construction and field-wise assertion is unaffected.
    """

    available: bool
    state: str
    reason: str
    expected_model: Optional[str] = None
    observed_model: Optional[str] = None
    latency_ms: Optional[int] = None
    checked_at: Optional[float] = None
    runtime_metadata: Optional[RuntimeModelMetadata] = None


class AlwaysAvailable:
    """Backwards-compatible availability implementation with no network I/O."""

    def check(self, tier: Tier) -> AvailabilityResult:
        if tier.metadata_source == METADATA_UPSTREAM:
            return AvailabilityResult(
                False, "unavailable", "upstream_metadata_not_configured"
            )
        return AvailabilityResult(
            True, "ready", "availability_not_configured",
            expected_model=tier.model if tier.model_identity else None,
        )

    def invalidate(self, tier_id: Optional[str] = None) -> None:
        return None


class HttpHealthAvailability:
    """Cached HTTP readiness probes for configured local tiers.

    The cache lock covers lookup and update only; the network call runs outside
    it so a slow probe for one tier never serializes unrelated tier checks.
    Concurrent cache misses are single-flighted per tier (ADR-0033): one thread
    probes while the rest return the last-known result, or fail closed with
    ``probe_pending`` when no result exists yet — never admitting to an
    unprobed tier and never stampeding a struggling serve.
    """

    def __init__(
        self,
        config: RouterConfig,
        *,
        opener: Optional[Callable[..., object]] = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._probe_interval = config.availability_probe_interval
        self._probe_timeout = config.availability_probe_timeout
        self._probe_max_bytes = config.availability_probe_max_bytes
        self._opener = opener if opener is not None else _direct_opener()
        self._clock = clock
        # Monotonic clock drives cache expiry; a separate wall clock stamps
        # ``checked_at`` so a readiness snapshot can render a real timestamp.
        self._wall_clock = wall_clock
        self._env = os.environ if env is None else env
        self._lock = threading.Lock()
        self._cache: Dict[str, tuple[float, AvailabilityResult]] = {}
        self._probe_locks: Dict[str, threading.Lock] = {}

    def _probe_lock(self, tier_id: str) -> threading.Lock:
        with self._lock:
            lock = self._probe_locks.get(tier_id)
            if lock is None:
                lock = self._probe_locks[tier_id] = threading.Lock()
            return lock

    def cached(self, tier_id: str) -> Optional[tuple[float, AvailabilityResult]]:
        """The last cached ``(age_seconds, result)`` for ``tier_id``, if any."""
        with self._lock:
            entry = self._cache.get(tier_id)
        if entry is None:
            return None
        return (max(0.0, self._clock() - entry[0]), entry[1])

    def probe_now(self, tier: Tier) -> AvailabilityResult:
        """Probe ``tier`` unconditionally and cache the stamped result."""
        url = self._health_url(tier)
        if url is None:
            return AvailabilityResult(True, "ready", "availability_not_configured")
        started = self._clock()
        result = self._probe(url, tier)
        result = replace(
            result,
            latency_ms=max(0, int(round((self._clock() - started) * 1000))),
            checked_at=self._wall_clock(),
        )
        with self._lock:
            self._cache[tier.id] = (self._clock(), result)
        return result

    @staticmethod
    def _health_url(tier: Tier) -> Optional[str]:
        if tier.privacy != PRIVACY_LOCAL or not tier.health_path:
            return None
        parsed = urlsplit(tier.base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, tier.health_path, "", ""))

    def check(self, tier: Tier) -> AvailabilityResult:
        url = self._health_url(tier)
        if url is None:
            return AvailabilityResult(True, "ready", "availability_not_configured")

        now = self._clock()
        with self._lock:
            cached = self._cache.get(tier.id)
            if cached is not None and now - cached[0] < self._probe_interval:
                return cached[1]

        probe_lock = self._probe_lock(tier.id)
        if not probe_lock.acquire(blocking=False):
            # Another thread is probing this tier right now. Serve the
            # last-known result (even if just expired) rather than duplicating
            # the probe; with no prior result, fail closed.
            with self._lock:
                cached = self._cache.get(tier.id)
            if cached is not None:
                return cached[1]
            return AvailabilityResult(False, "unavailable", "probe_pending")
        try:
            # Re-check under the flight lock: the winner may have refreshed the
            # cache while this thread waited on the non-blocking acquire.
            now = self._clock()
            with self._lock:
                cached = self._cache.get(tier.id)
                if cached is not None and now - cached[0] < self._probe_interval:
                    return cached[1]
            # Stamp freshness metadata so a readiness snapshot reports when
            # this serve was last probed and how long it took. Cached and
            # returned together, so a subsequent cache hit reflects the ACTUAL
            # last probe, not the read time.
            return self.probe_now(tier)
        finally:
            probe_lock.release()

    @staticmethod
    def _models_url(tier: Tier) -> str:
        parsed = urlsplit(tier.base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1/models", "", ""))

    @staticmethod
    def _props_url(tier: Tier) -> str:
        parsed = urlsplit(tier.base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/props", "", ""))

    def _probe(self, url: str, tier: Tier) -> AvailabilityResult:
        request = urllib.request.Request(url, method="GET")
        try:
            with self._opener(request, timeout=self._probe_timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
        except urllib.error.HTTPError as exc:
            return AvailabilityResult(False, "unavailable", f"health_http_{exc.code}")
        except Exception as exc:  # noqa: BLE001 - all transport faults are readiness failures
            return AvailabilityResult(
                False,
                "unavailable",
                f"health_transport_{type(exc).__name__}",
            )

        if isinstance(status, int) and 200 <= status < 400:
            if tier.model_identity or tier.metadata_source == METADATA_UPSTREAM:
                return self._probe_identity(tier)
            return AvailabilityResult(True, "ready", "health_passed")
        code = status if isinstance(status, int) else "unknown"
        return AvailabilityResult(False, "unavailable", f"health_http_{code}")

    def _probe_identity(self, tier: Tier) -> AvailabilityResult:
        expected = tier.model
        headers = {"Accept": "application/json"}
        token = self._env.get(tier.auth_env, "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self._models_url(tier), headers=headers, method="GET"
        )
        try:
            with self._opener(request, timeout=self._probe_timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                if not isinstance(status, int) or not 200 <= status < 300:
                    code = status if isinstance(status, int) else "unknown"
                    return AvailabilityResult(
                        False, "unavailable", f"identity_http_{code}", expected
                    )
                payload = response.read(self._probe_max_bytes + 1)
        except urllib.error.HTTPError as exc:
            return AvailabilityResult(
                False, "unavailable", f"identity_http_{exc.code}", expected
            )
        except Exception as exc:  # noqa: BLE001 - stable transport code only
            return AvailabilityResult(
                False,
                "unavailable",
                f"identity_transport_{type(exc).__name__}",
                expected,
            )
        if len(payload) > self._probe_max_bytes:
            return AvailabilityResult(
                False, "unavailable", "identity_oversized", expected
            )
        try:
            document = json.loads(payload)
            data = document["data"]
            if not isinstance(data, list):
                raise ValueError("bad data")
            model_entries = [
                item
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            model_ids = [item["id"] for item in model_entries]
        except Exception:  # noqa: BLE001 - raw parser details are not status
            return AvailabilityResult(
                False, "unavailable", "identity_malformed", expected
            )
        observed = model_ids[0][:256] if model_ids else None
        if tier.metadata_source == METADATA_UPSTREAM:
            if len(model_entries) != 1:
                return AvailabilityResult(
                    False,
                    "unavailable",
                    "upstream_metadata_model_count",
                    None,
                    observed,
                )
            return self._runtime_metadata_result(
                tier,
                model_entries[0],
                headers=headers,
            )
        if expected in model_ids:
            return AvailabilityResult(
                True, "ready", "identity_passed", expected, expected
            )
        return AvailabilityResult(
            False, "unavailable", "identity_mismatch", expected, observed
        )

    @staticmethod
    def _positive_int(value: object, *, maximum: int = 10_000_000) -> Optional[int]:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 < value <= maximum
        ):
            return None
        return value

    @staticmethod
    def _safe_metadata_text(value: object) -> Optional[str]:
        if not isinstance(value, str) or _SAFE_METADATA_TEXT_RE.fullmatch(value) is None:
            return None
        return value

    @classmethod
    def _context_from_model_card(cls, entry: Mapping[str, object]) -> Optional[int]:
        for key in _CONTEXT_KEYS:
            value = cls._positive_int(entry.get(key))
            if value is not None:
                return value
        return None

    def _read_props(
        self, tier: Tier, *, headers: Mapping[str, str]
    ) -> Optional[Mapping[str, object]]:
        request = urllib.request.Request(
            self._props_url(tier), headers=dict(headers), method="GET"
        )
        try:
            with self._opener(request, timeout=self._probe_timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                if not isinstance(status, int) or not 200 <= status < 300:
                    return None
                payload = response.read(self._probe_max_bytes + 1)
        except Exception:  # noqa: BLE001 - absence is a stable metadata miss
            return None
        if len(payload) > self._probe_max_bytes:
            return None
        try:
            document = json.loads(payload)
        except Exception:  # noqa: BLE001 - raw parser details stay private
            return None
        return document if isinstance(document, Mapping) else None

    @classmethod
    def _modalities_from_props(cls, props: Mapping[str, object]) -> tuple[str, ...]:
        raw = props.get("modalities")
        if not isinstance(raw, Mapping):
            return ()
        modalities = {"text"}
        if raw.get("vision") is True or raw.get("image") is True:
            modalities.add("image")
        for name in ("video", "audio"):
            if raw.get(name) is True:
                modalities.add(name)
        return tuple(sorted(modalities))

    def _runtime_metadata_result(
        self,
        tier: Tier,
        entry: Mapping[str, object],
        *,
        headers: Mapping[str, str],
    ) -> AvailabilityResult:
        model = self._safe_metadata_text(entry.get("id"))
        if model is None:
            return AvailabilityResult(
                False, "unavailable", "upstream_metadata_model_invalid"
            )

        context_limit = self._context_from_model_card(entry)
        engine = self._safe_metadata_text(entry.get("owned_by"))
        quantization = None
        engine_version = None
        max_concurrency = None
        modalities: tuple[str, ...] = ()

        # vLLM and SGLang publish max_model_len in the OpenAI model card.
        # llama.cpp publishes the running context and additional descriptive
        # facts from its read-only GET /props endpoint.
        props = None
        if context_limit is None or engine == "llamacpp":
            props = self._read_props(tier, headers=headers)
        if props is not None:
            props_alias = self._safe_metadata_text(props.get("model_alias"))
            if props_alias is not None and props_alias != model:
                return AvailabilityResult(
                    False,
                    "unavailable",
                    "upstream_metadata_identity_mismatch",
                    None,
                    model,
                )
            settings = props.get("default_generation_settings")
            if context_limit is None and isinstance(settings, Mapping):
                context_limit = self._positive_int(settings.get("n_ctx"))
            quantization = self._safe_metadata_text(props.get("model_ftype"))
            engine_version = self._safe_metadata_text(props.get("build_info"))
            max_concurrency = self._positive_int(
                props.get("total_slots"), maximum=100_000
            )
            modalities = self._modalities_from_props(props)

        if context_limit is None:
            return AvailabilityResult(
                False,
                "unavailable",
                "upstream_metadata_context_missing",
                None,
                model,
            )
        if (
            tier.max_output_tokens is not None
            and tier.max_output_tokens > context_limit
        ):
            return AvailabilityResult(
                False,
                "unavailable",
                "upstream_metadata_output_limit_conflict",
                None,
                model,
            )
        metadata = RuntimeModelMetadata(
            model=model,
            context_limit=context_limit,
            engine=engine,
            quantization=quantization,
            engine_version=engine_version,
            max_concurrency=max_concurrency,
            modalities=modalities,
        )
        return AvailabilityResult(
            True,
            "ready",
            "upstream_metadata_passed",
            None,
            model,
            runtime_metadata=metadata,
        )

    def invalidate(self, tier_id: Optional[str] = None) -> None:
        """Expire cached state for tests and future lifecycle notifications."""
        with self._lock:
            if tier_id is None:
                self._cache.clear()
            else:
                self._cache.pop(tier_id, None)


def safe_check(
    availability,
    tier: Tier,
    *,
    reason_prefix: str = "availability_check",
    include_exception_name: bool = True,
) -> AvailabilityResult:
    """Call ``availability.check(tier)``, coercing any fault into an unavailable result.

    Shared by every call site that needs a tier's readiness but must never let a
    broken ``availability`` implementation (a raised exception, or a non-
    :class:`AvailabilityResult` return) escape as a 500 or crash routing.
    ``include_exception_name`` selects between the request-path reason
    (``{reason_prefix}_{ExceptionName}``, useful for debugging a live fault) and
    the fixed, content-free reason used by read-only status endpoints
    (``{reason_prefix}_failed``).
    """
    try:
        result = availability.check(tier)
        if not isinstance(result, AvailabilityResult):
            raise TypeError("non-AvailabilityResult")
        return result
    except Exception as exc:  # noqa: BLE001 - a broken availability must never propagate
        reason = (
            f"{reason_prefix}_{type(exc).__name__}"
            if include_exception_name
            else f"{reason_prefix}_failed"
        )
        return AvailabilityResult(False, "unavailable", reason)


def resolve_runtime_tier(
    tier: Tier, readiness: AvailabilityResult
) -> Optional[Tier]:
    """Return the effective runtime tier, or ``None`` when discovery is incomplete."""
    if tier.metadata_source != METADATA_UPSTREAM:
        return tier
    metadata = readiness.runtime_metadata
    if not readiness.available or metadata is None:
        return None
    return replace(
        tier,
        model=metadata.model,
        context_limit=metadata.context_limit,
        engine=metadata.engine,
        quantization=metadata.quantization,
        max_concurrency=(
            tier.max_concurrency
            if tier.max_concurrency is not None
            else metadata.max_concurrency
        ),
    )


__all__ = [
    "AlwaysAvailable",
    "AvailabilityResult",
    "HttpHealthAvailability",
    "RuntimeModelMetadata",
    "resolve_runtime_tier",
    "safe_check",
]
