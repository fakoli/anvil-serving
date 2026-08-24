"""OpenAI ``/v1/models`` discovery for configured direct capabilities."""
from __future__ import annotations

from typing import Iterable

from .availability import resolve_runtime_tier, safe_check
from .config import RouterConfig, Tier, normalize_model_alias

OWNED_BY = "anvil-serving"
# Fixed epoch keeps discovery byte-stable for tests and clients' HTTP caches.
CREATED = 1_700_000_000


def model_route_entry(alias: str, tier: Tier | None = None) -> dict:
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
    for alias in aliases:
        normalized = normalize_model_alias(alias)
        if normalized and normalized not in seen:
            tier = (
                config.tier(config.model_routes[alias])
                if config is not None
                else None
            )
            if tier is not None and availability is not None:
                readiness = safe_check(
                    availability, tier, include_exception_name=False
                )
                tier = resolve_runtime_tier(tier, readiness) or tier
            entries.append(model_route_entry(alias, tier))
            seen.add(normalized)
    return {"object": "list", "data": entries}
