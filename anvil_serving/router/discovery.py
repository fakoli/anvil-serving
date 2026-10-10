"""OpenAI ``/v1/models`` discovery for configured direct capabilities."""
from __future__ import annotations

from typing import Iterable, Mapping

from .availability import resolve_runtime_tier, safe_check
from .config import RouterConfig, Tier, normalize_model_alias
from .model_capacity import _nonnegative_int, replica_metadata

OWNED_BY = "anvil-serving"
# Fixed epoch keeps discovery byte-stable for tests and clients' HTTP caches.
CREATED = 1_700_000_000


def _declared_media_limits(tier: Tier) -> dict:
    """Declared per-request media limits from tier params, when present.

    Mirrors the authenticated capability surface (``limits`` in
    ``/v1/models/capabilities``) so OpenAI-compatible clients can budget
    image payloads from plain discovery without a second request.
    Undeclared limits stay out of the payload to keep discovery byte-stable.
    """
    if not isinstance(tier.params, Mapping):
        return {}
    capabilities = tier.params.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return {}
    result: dict[str, int] = {}
    for key in ("images_per_request", "video_per_request"):
        value = _nonnegative_int(capabilities.get(key))
        if value is not None:
            result[key] = value
    return result


def model_route_entry(
    alias: str, tier: Tier | None = None, replica: dict | None = None
) -> dict:
    """Build one OpenAI-shaped model entry for a configured caller alias."""
    entry = {
        "id": alias,
        "object": "model",
        "name": alias,
        "description": "Configured serving capability",
        "owned_by": OWNED_BY,
        "created": CREATED,
    }
    if tier is not None:
        # ``context_window`` is understood by Hermes and other OpenAI-compatible
        # discovery clients.  The more detailed authenticated capability surface
        # remains authoritative for modalities, readiness, and fingerprints.
        entry["context_window"] = tier.context_limit if tier.context_limit > 0 else None
        entry["max_output_tokens"] = tier.max_output_tokens
        # Declared per-request media limits, when the operator set them, so
        # clients can cap image payloads without probing the backend.
        entry.update(_declared_media_limits(tier))
    if replica is not None and tier is not None:
        entry["logical_tier"] = tier.id
        for key in (
            "members",
            "replica_identity",
            "deployment_identity_source",
            "runtime_deployment_identity_verified",
        ):
            entry[key] = replica[key]
    return entry


def models_payload(
    model_routes: RouterConfig | Iterable[str], availability: object = None
) -> dict:
    """Build the closed configured model list.

    Discovery deliberately does not synthesize presets, tier ids, or implicit
    aliases.  A caller can use only an advertised alias (subject to the same
    wire normalization the request router uses).
    """
    config = model_routes if isinstance(model_routes, RouterConfig) else None
    aliases = config.model_routes if config is not None else model_routes
    entries = []
    seen: set[str] = set()
    replica_cache: dict[str, dict] = {}
    for alias in aliases:
        normalized = normalize_model_alias(alias)
        if normalized and normalized not in seen:
            tier = (
                config.tier(config.model_routes[alias])
                if config is not None
                else None
            )
            replica = None
            if tier is not None and tier.replicas:
                replica = replica_cache.get(tier.id)
                if replica is None:
                    replica, _readiness = replica_metadata(tier, availability)
                    replica_cache[tier.id] = replica
            elif tier is not None and availability is not None:
                readiness = safe_check(
                    availability, tier, include_exception_name=False
                )
                tier = resolve_runtime_tier(tier, readiness) or tier
            entries.append(model_route_entry(alias, tier, replica))
            seen.add(normalized)
    return {"object": "list", "data": entries}
