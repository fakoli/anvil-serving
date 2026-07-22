"""Live per-tier / per-serve health snapshot for ``GET /v1/health/tiers`` (#292).

The router already tracks runtime tier readiness for routing: the cached,
bounded :mod:`~anvil_serving.router.availability` probe is what produces
``skipped-unavailable`` / ``health_transport_*`` in the decision log.  Overall
``/health`` and the ``/v1/decisions`` log, however, cannot tell a *configured
but idle* tier apart from a *down* one — a tier that has not been routed to
recently simply does not appear.

This module surfaces that already-tracked readiness as a first-class snapshot
covering EVERY configured serve — chat ``llm`` tiers (``[[router.tiers]]``),
purpose models (``[[router.purpose_models]]``), and audio routes
(``[[router.audio_routes]]``) — not only recently-routed ones.  It reuses the
SAME cached availability checker, so polling the endpoint never adds a heavy new
probe path: repeated reads inside ``availability_probe_interval`` (default 5s)
return the cached readiness rather than re-hitting a serve.

**No secrets leave here.**  A row carries only a serve ``id`` (operator-chosen,
never a host), a fixed ``role`` label, a coarse ``status``, optional freshness
(``last_check`` / ``latency_ms``), and a content-free ``reason`` *category*.  A
serve URL, host, upstream token, or model id is never emitted, and a ``reason``
is forwarded verbatim only when it matches the bounded category vocabulary —
anything else (a stray host:port, a raw exception, an accidental secret) is
replaced with a generic category.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from .availability import AvailabilityResult
from .config import PRIVACY_LOCAL, PURPOSE_EMBEDDING, RouterConfig, Tier

#: Role label for every chat tier (``[[router.tiers]]``); heavy/fast are both
#: ``llm``.  Purpose/audio serves derive their role from their own kind/purpose.
ROLE_LLM = "llm"

#: OpenAI-style serves answer ``GET /v1/models``; reusing it as the readiness
#: path for non-tier serves keeps ONE cached probe machinery for every surface.
_SERVE_HEALTH_PATH = "/v1/models"

#: A ``reason`` is forwarded verbatim only when it is a bounded, content-free
#: category token (letters / digits / underscore, like
#: ``health_transport_URLError``).  Anything else fails this match and is
#: replaced, so the endpoint cannot leak an upstream host, URL, or secret even
#: if a custom availability implementation returns a dirty reason.
_SAFE_REASON_RE = re.compile(r"[A-Za-z0-9_]{1,64}")

#: Fallback category emitted when a reason is missing the safe-category shape.
_GENERIC_REASON = "unavailable"


def _sanitize_reason(reason: Optional[str]) -> Optional[str]:
    """Return ``reason`` only when it is a safe category token, else a generic one."""
    if not reason:
        return None
    return reason if _SAFE_REASON_RE.fullmatch(reason) else _GENERIC_REASON


def _status(result: AvailabilityResult) -> str:
    """Map a tracked readiness result to ``up`` / ``degraded`` / ``down``.

    ``up`` is available (including a serve with no configured probe, which
    routing also treats as available).  A serve that responded but is serving
    the wrong identity is ``degraded`` — reachable, not trustworthy.  Everything
    else unavailable (unreachable, health/transport fault) is ``down``.
    """
    if result.available:
        return "up"
    if (result.reason or "").startswith("identity_mismatch"):
        return "degraded"
    return "down"


def _iso_utc(checked_at: Optional[float]) -> Optional[str]:
    if not isinstance(checked_at, (int, float)):
        return None
    stamp = datetime.fromtimestamp(float(checked_at), tz=timezone.utc)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _row(serve_id: str, role: str, result: AvailabilityResult) -> dict:
    """Assemble one secret-free health row from a tracked readiness result."""
    latency_ms = result.latency_ms
    if not isinstance(latency_ms, int) or isinstance(latency_ms, bool):
        latency_ms = None
    return {
        "id": serve_id,
        "role": role,
        "status": _status(result),
        "last_check": _iso_utc(result.checked_at),
        "latency_ms": latency_ms,
        "reason": _sanitize_reason(result.reason),
    }


def _serve_probe_target(serve_id: str, base_url: str, auth_env: Optional[str]) -> Tier:
    """A minimal :class:`Tier` so a non-tier serve reuses the availability probe.

    ``model_identity`` stays ``False`` so the probe is a bare ``GET /v1/models``
    that never sends an upstream token, and ``id`` is namespaced so the shared
    availability cache never collides with a real tier id.
    """
    return Tier(
        id=serve_id,
        base_url=base_url,
        dialect="openai",
        context_limit=0,
        privacy=PRIVACY_LOCAL,
        tool_support=False,
        auth_env=auth_env or "",
        health_path=_SERVE_HEALTH_PATH,
        model_identity=False,
    )


def _safe_check(availability, tier: Tier) -> AvailabilityResult:
    """Resolve one serve's readiness, failing closed to ``down`` on any fault.

    A faulty availability implementation must never turn a health read into a
    500 or leak an exception string; it becomes a content-free ``down`` row,
    mirroring ``RoutingBackend._availability_snapshot``.
    """
    try:
        result = availability.check(tier)
        if not isinstance(result, AvailabilityResult):
            raise TypeError("non-AvailabilityResult")
        return result
    except Exception as exc:  # noqa: BLE001 - readiness failure isolates the serve
        return AvailabilityResult(
            False, "unavailable", f"availability_check_{type(exc).__name__}"
        )


def build_tier_health(config: RouterConfig, availability) -> dict:
    """Return ``{"tiers": [row, ...]}`` for EVERY configured serve.

    Enumerates all configured surfaces (never only recently-routed ones) and
    reuses ``availability`` — the same cached probe routing relies on — so the
    snapshot is live but cheap.  Order is stable: chat tiers, then purpose
    models, then audio routes, in configuration order.
    """
    rows = []
    for tier in config.tiers:
        rows.append(_row(tier.id, ROLE_LLM, _safe_check(availability, tier)))
    for pm in config.purpose_models:
        role = "embeddings" if pm.kind == PURPOSE_EMBEDDING else pm.kind
        target = _serve_probe_target(f"purpose:{pm.id}", pm.base_url, pm.auth_env)
        rows.append(_row(pm.id, role, _safe_check(availability, target)))
    for route in config.audio_routes:
        target = _serve_probe_target(f"audio:{route.id}", route.base_url, route.auth_env)
        rows.append(_row(route.id, route.purpose, _safe_check(availability, target)))
    return {"tiers": rows}


__all__ = ["build_tier_health", "ROLE_LLM"]
