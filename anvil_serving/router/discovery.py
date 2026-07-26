"""OpenAI ``/v1/models`` discovery for configured direct capabilities."""
from __future__ import annotations

from typing import Iterable

from .config import normalize_model_alias

OWNED_BY = "anvil-serving"
# Fixed epoch keeps discovery byte-stable for tests and clients' HTTP caches.
CREATED = 1_700_000_000


def model_route_entry(alias: str) -> dict:
    """Build one OpenAI-shaped model entry for a configured caller alias."""
    return {
        "id": alias,
        "object": "model",
        "name": alias,
        "description": "Configured serving capability",
        "owned_by": OWNED_BY,
        "created": CREATED,
    }


def models_payload(model_routes: Iterable[str]) -> dict:
    """Build the closed configured model list.

    Discovery deliberately does not synthesize presets, tier ids, or implicit
    aliases.  A caller can use only an advertised alias (subject to the same
    wire normalization the request router uses).
    """
    entries = []
    seen: set[str] = set()
    for alias in model_routes:
        normalized = normalize_model_alias(alias)
        if normalized and normalized not in seen:
            entries.append(model_route_entry(alias))
            seen.add(normalized)
    return {"object": "list", "data": entries}
