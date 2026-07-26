"""OpenAI ``/v1/models`` preset discovery (harness-router:T004).

Serves the router's caller-facing vocabulary — the canonical *presets* from
:mod:`anvil_serving.router.intent` (the R002 enum) — as an OpenAI-shaped model
list, so a harness model picker (e.g. Claude Code's ``/model`` with
``CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1``) can discover the intent tokens
it may address. Intent-addressed presets ARE the "models" this router exposes.

Single source of truth: this module DERIVES the list from
:data:`~anvil_serving.router.intent.PRESETS`. It never re-declares the preset
set, so adding/removing a preset at the canonical source changes ``/v1/models``
with no edit here (the T004 no-drift criterion).

Stdlib-only and deterministic: the OpenAI ``Model`` object carries a ``created``
unix-seconds field, but presets are config-versioned rather than time-born, so we
pin a fixed constant instead of calling ``time.now()`` — keeping the payload
byte-stable for tests and HTTP caches.
"""
from __future__ import annotations

from typing import Iterable

from .intent import PRESETS, Preset, parse_model

OWNED_BY = "anvil-serving"

#: The standalone routing-decision endpoint (POST /v1/route, advise-and-defer:T007).
#: Advertised here alongside the preset vocabulary so harnesses can discover it
#: without hard-coding the path (the ``GET /healthz`` response includes it).
ROUTE_ENDPOINT = "/v1/route"

# Fixed, deterministic creation epoch (2023-11-14T22:13:20Z) for every entry.
# Constant on purpose — see the module docstring.
CREATED = 1_700_000_000


def model_entry(preset: Preset) -> dict:
    """Build one OpenAI ``Model`` object from a canonical :class:`Preset`."""
    return {
        "id": preset.id,
        "object": "model",
        "name": preset.name,
        "description": preset.description,
        "owned_by": OWNED_BY,
        "created": CREATED,
    }


def model_route_entry(alias: str) -> dict:
    """Build one discovery entry for a configured exact model alias."""
    return {
        "id": alias,
        "object": "model",
        "name": alias,
        "description": "Configured direct model route",
        "owned_by": OWNED_BY,
        "created": CREATED,
    }


def models_payload(
    presets: Iterable[Preset] = PRESETS,
    model_routes: Iterable[str] = (),
) -> dict:
    """Build the OpenAI ``/v1/models`` response from the canonical presets.

    ``presets`` defaults to :data:`~anvil_serving.router.intent.PRESETS` — the
    single source of truth — so the served list always tracks the R002 enum. The
    shape is OpenAI's list envelope so off-the-shelf OpenAI clients (and the
    Claude Code gateway model picker) parse it directly.
    """
    entries = [model_entry(p) for p in presets]
    seen = {parse_model(entry["id"]) for entry in entries}
    for alias in model_routes:
        normalized = parse_model(alias)
        if normalized and normalized not in seen:
            entries.append(model_route_entry(alias))
            seen.add(normalized)
    return {"object": "list", "data": entries}
