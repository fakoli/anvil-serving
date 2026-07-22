"""Runtime tier availability for health-aware routing.

Router configuration describes which upstreams *may* serve a request.  This
module answers the narrower runtime question: is a configured local upstream
ready right now?  A cached, bounded HTTP health probe keeps a stopped or
starting model container out of the request path without rewriting router TOML
or teaching the router how to operate Docker.

The default implementation is deliberately conservative and backwards
compatible:

* cloud tiers are not probed;
* local tiers without ``health_path`` are treated as available;
* configured probes use the tier's scheme/authority and replace only the path;
* probe failures return structured state and never raise into routing;
* results are cached for ``probe_interval`` seconds to avoid request-time probe
  storms, and a recovered endpoint is automatically readmitted after expiry.

This is readiness, not quality.  A structurally bad model response still flows
through the independent verifier/profile machinery and must not be confused
with endpoint availability.
"""
from __future__ import annotations

import threading
import time
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable, Dict, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .config import PRIVACY_LOCAL, RouterConfig, Tier


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


class AlwaysAvailable:
    """Backwards-compatible availability implementation with no network I/O."""

    def check(self, tier: Tier) -> AvailabilityResult:
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
    Concurrent cache misses may issue duplicate probes, which is bounded by the
    front-door concurrency limit and preferable to blocking the full router.
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

        started = self._clock()
        result = self._probe(url, tier)
        # Stamp freshness metadata so a readiness snapshot reports when this serve
        # was last probed and how long it took. Cached and returned together, so a
        # subsequent cache hit reflects the ACTUAL last probe, not the read time.
        result = replace(
            result,
            latency_ms=max(0, int(round((self._clock() - started) * 1000))),
            checked_at=self._wall_clock(),
        )
        with self._lock:
            self._cache[tier.id] = (self._clock(), result)
        return result

    @staticmethod
    def _models_url(tier: Tier) -> str:
        parsed = urlsplit(tier.base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/v1/models", "", ""))

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
            if tier.model_identity:
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
            model_ids = [
                item.get("id") for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
        except Exception:  # noqa: BLE001 - raw parser details are not status
            return AvailabilityResult(
                False, "unavailable", "identity_malformed", expected
            )
        observed = model_ids[0][:256] if model_ids else None
        if expected in model_ids:
            return AvailabilityResult(
                True, "ready", "identity_passed", expected, expected
            )
        return AvailabilityResult(
            False, "unavailable", "identity_mismatch", expected, observed
        )

    def invalidate(self, tier_id: Optional[str] = None) -> None:
        """Expire cached state for tests and future lifecycle notifications."""
        with self._lock:
            if tier_id is None:
                self._cache.clear()
            else:
                self._cache.pop(tier_id, None)


__all__ = [
    "AlwaysAvailable",
    "AvailabilityResult",
    "HttpHealthAvailability",
]
