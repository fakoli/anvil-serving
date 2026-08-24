"""Secret-free read-only metadata projections for configured chat models.

The router owns aliases, declared limits, and readiness.  This module makes a
small, deliberately allowlisted view of those facts available to a front-door
endpoint without exposing tier URLs, auth environment names, or arbitrary
``tier.params`` values.  It performs no network or filesystem I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Mapping, Optional

from anvil_serving import __version__

from .availability import AvailabilityResult, resolve_runtime_tier, safe_check
from .config import METADATA_UPSTREAM, RouterConfig, Tier, normalize_model_alias
from .model_capacity import _nonnegative_int


CAPABILITIES_PARAMS_KEY = "capabilities"
FINGERPRINT_PARAMS_KEY = "fingerprint"
_READINESS_REASON_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
_SAFE_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+ -]{0,127}")
_MODALITY_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_FINGERPRINT_KEYS = (
    "model_revision",
    "engine_version",
    "image_digest",
    "config_fingerprint",
)


def _params_section(tier: Tier, key: str) -> Mapping[str, object]:
    if not isinstance(tier.params, Mapping):
        return {}
    section = tier.params.get(key)
    return section if isinstance(section, Mapping) else {}


def _safe_text(value: object) -> Optional[str]:
    if (
        not isinstance(value, str)
        or "://" in value
        or _SAFE_TEXT_RE.fullmatch(value) is None
    ):
        return None
    return value


def _safe_modalities(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return sorted({item for item in value if isinstance(item, str) and _MODALITY_RE.fullmatch(item)})


def _compat(value: object) -> Optional[dict]:
    """Allowlist the OpenClaw-compatible ``compat`` capability declarations.

    Currently emits ``supportsUsageInStreaming`` and ``supportsStrictMode`` (both
    bools).  Everything else in the source mapping is intentionally dropped so
    arbitrary ``params`` values never leak into the public capabilities payload.
    """
    if not isinstance(value, Mapping):
        return None
    result: dict[str, object] = {}
    supports = value.get("supportsUsageInStreaming")
    if isinstance(supports, bool):
        result["supportsUsageInStreaming"] = supports
    strict = value.get("supportsStrictMode")
    if isinstance(strict, bool):
        result["supportsStrictMode"] = strict
    # Reasoning-effort ladder. Uses the EXACT OpenClaw compat key and accepts a
    # list as a SET: order is not semantically meaningful to OpenClaw (it treats
    # the list as a membership set), so we preserve source order for readability
    # but document that consumers must not read it as an ordered ladder.
    efforts = value.get("supportedReasoningEfforts")
    if isinstance(efforts, (list, tuple)):
        seen = set()
        cleaned = []
        for e in efforts:
            if isinstance(e, str) and e not in seen and _MODALITY_RE.fullmatch(e):
                seen.add(e)
                cleaned.append(e)
        if cleaned:
            result["supportedReasoningEfforts"] = cleaned
    return result or None


def _thinking(value: object) -> Optional[dict]:
    if not isinstance(value, Mapping):
        return None
    result = {}
    supported = value.get("supported")
    if isinstance(supported, bool):
        result["supported"] = supported
    default = value.get("default")
    if isinstance(default, str) and _MODALITY_RE.fullmatch(default):
        result["default"] = default
    caller_override = value.get("caller_override")
    if isinstance(caller_override, bool):
        result["caller_override"] = caller_override
    max_tokens = value.get("max_tokens")
    if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and 0 < max_tokens <= 10_000_000:
        result["max_tokens"] = max_tokens
    return result or None


def _readiness(availability: object, tier: Tier) -> AvailabilityResult:
    return safe_check(availability, tier, include_exception_name=False)


def _safe_readiness(
    availability: object,
    tier: Tier,
    result: Optional[AvailabilityResult] = None,
) -> dict:
    result = result or _readiness(availability, tier)
    return {
        "loaded": result.available,
        "state": (
            result.state
            if _MODALITY_RE.fullmatch(result.state or "")
            else "unavailable"
        ),
        "reason": (
            result.reason
            if _READINESS_REASON_RE.fullmatch(result.reason or "")
            else "unavailable"
        ),
    }


def _aliases_by_tier(config: RouterConfig) -> Mapping[str, list[str]]:
    result: dict[str, list[str]] = {}
    for alias, tier_id in config.model_routes.items():
        result.setdefault(tier_id, []).append(alias)
    return {tier_id: sorted(aliases) for tier_id, aliases in result.items()}


def _selected_alias(config: RouterConfig, query: Mapping[str, list[str]]) -> Optional[str]:
    if set(query) - {"model"}:
        raise ValueError("unsupported query parameter")
    values = query.get("model")
    if values is None:
        return None
    if len(values) != 1:
        raise ValueError("model must be specified once")
    alias = normalize_model_alias(values[0])
    if alias not in config.model_routes:
        raise KeyError(alias)
    return alias


def build_model_capabilities(
    config: RouterConfig, availability: object, query: Mapping[str, list[str]]
) -> dict:
    """Return allowlisted declared capabilities for configured chat tiers."""
    selected_alias = _selected_alias(config, query)
    aliases_by_tier = _aliases_by_tier(config)
    rows = []
    for tier in config.tiers:
        aliases = aliases_by_tier.get(tier.id, [])
        if selected_alias is not None and selected_alias not in aliases:
            continue
        capabilities = _params_section(tier, CAPABILITIES_PARAMS_KEY)
        readiness = _readiness(availability, tier)
        effective = resolve_runtime_tier(tier, readiness)
        reported = effective or tier
        rows.append({
            "object": "model_capabilities",
            "id": tier.id,
            "aliases": aliases,
            "model": (
                reported.model
                if tier.metadata_source != METADATA_UPSTREAM or effective is not None
                else None
            ),
            "context_limit_tokens": (
                reported.context_limit
                if reported.context_limit > 0
                else None
            ),
            "tools": {"supported": tier.tool_support},
            "modalities": _safe_modalities(capabilities.get("modalities")),
            "thinking": _thinking(capabilities.get("thinking")),
            "compat": _compat(capabilities.get("compat")),
            "limits": {
                "max_output_tokens": tier.max_output_tokens,
                "images_per_request": _nonnegative_int(capabilities.get("images_per_request")),
                "video_per_request": _nonnegative_int(capabilities.get("video_per_request")),
            },
            "readiness": _safe_readiness(availability, tier, readiness),
        })
    return {"object": "list", "data": rows}


def build_model_fingerprints(
    config: RouterConfig, availability: object, query: Mapping[str, list[str]]
) -> dict:
    """Return allowlisted build identity and observed readiness identity."""
    selected_alias = _selected_alias(config, query)
    aliases_by_tier = _aliases_by_tier(config)
    rows = []
    for tier in config.tiers:
        aliases = aliases_by_tier.get(tier.id, [])
        if selected_alias is not None and selected_alias not in aliases:
            continue
        readiness = _readiness(availability, tier)
        effective = resolve_runtime_tier(tier, readiness)
        reported = effective or tier
        fingerprint = (
            {}
            if tier.metadata_source == METADATA_UPSTREAM
            else _params_section(tier, FINGERPRINT_PARAMS_KEY)
        )
        rows.append({
            "object": "model_fingerprint",
            "id": tier.id,
            "aliases": aliases,
            "model": (
                reported.model
                if tier.metadata_source != METADATA_UPSTREAM or effective is not None
                else None
            ),
            "fingerprint": {key: _safe_text(fingerprint.get(key)) for key in _FINGERPRINT_KEYS},
            "served_identity": {
                "expected": _safe_text(readiness.expected_model),
                "observed": _safe_text(readiness.observed_model),
            },
            "served_configuration": {
                "metadata_source": tier.metadata_source,
                "context_limit_tokens": (
                    reported.context_limit
                    if reported.context_limit > 0
                    else None
                ),
                "engine": reported.engine,
                "quantization": reported.quantization,
                "engine_version": (
                    readiness.runtime_metadata.engine_version
                    if readiness.runtime_metadata is not None
                    else None
                ),
                "max_concurrency": (
                    readiness.runtime_metadata.max_concurrency
                    if readiness.runtime_metadata is not None
                    else tier.max_concurrency
                ),
                "modalities": (
                    list(readiness.runtime_metadata.modalities)
                    if readiness.runtime_metadata is not None
                    else []
                ),
            },
            "readiness": {
                "loaded": readiness.available,
                "state": (
                    readiness.state
                    if _MODALITY_RE.fullmatch(readiness.state or "")
                    else "unavailable"
                ),
                "reason": (
                    readiness.reason
                    if _READINESS_REASON_RE.fullmatch(readiness.reason or "")
                    else "unavailable"
                ),
            },
        })
    return {"object": "list", "data": rows}


def _canonical_public_config(config: RouterConfig) -> bytes:
    """Serialize only safe, emitted topology facts for a stable config hash."""
    aliases_by_tier = _aliases_by_tier(config)
    tiers = []
    for tier in sorted(config.tiers, key=lambda item: item.id):
        capabilities = _params_section(tier, CAPABILITIES_PARAMS_KEY)
        fingerprint = _params_section(tier, FINGERPRINT_PARAMS_KEY)
        tiers.append({
            "id": tier.id,
            "aliases": aliases_by_tier.get(tier.id, []),
            "model": tier.model,
            "metadata_source": tier.metadata_source,
            "dialect": tier.dialect,
            "context_limit_tokens": (
                None
                if tier.metadata_source == METADATA_UPSTREAM
                else tier.context_limit
            ),
            "max_output_tokens": tier.max_output_tokens,
            "tool_support": tier.tool_support,
            "engine": tier.engine,
            "quantization": tier.quantization,
            "model_identity": tier.model_identity,
            "capabilities": {
                "modalities": _safe_modalities(capabilities.get("modalities")),
                "thinking": _thinking(capabilities.get("thinking")),
                "compat": _compat(capabilities.get("compat")),
                "images_per_request": _nonnegative_int(capabilities.get("images_per_request")),
                "video_per_request": _nonnegative_int(capabilities.get("video_per_request")),
            },
            "fingerprint": {key: _safe_text(fingerprint.get(key)) for key in _FINGERPRINT_KEYS},
        })
    payload = {"model_routes": sorted(config.model_routes.items()), "tiers": tiers}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_router_status(
    config: RouterConfig,
    *,
    started_at: float,
    now: Optional[float] = None,
    package_version: str = __version__,
) -> dict:
    """Return a safe router process and topology status snapshot.

    ``started_at`` is injected by the process owner, keeping this projection
    deterministic and independently testable.
    """
    observed_at = time.time() if now is None else now
    started = datetime.fromtimestamp(started_at, timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "object": "router_status",
        "package_version": package_version,
        "started_at": started,
        "uptime_seconds": max(0, int(observed_at - started_at)),
        "model_aliases": sorted(config.model_routes),
        "tier_counts": {"configured": len(config.tiers), "enabled": len(set(config.model_routes.values()))},
        "config_sha256": hashlib.sha256(_canonical_public_config(config)).hexdigest(),
    }


__all__ = [
    "build_model_capabilities",
    "build_model_fingerprints",
    "build_router_status",
]
