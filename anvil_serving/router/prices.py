"""Optional cost-sync: fetch per-model pricing from the free LiteLLM JSON (stdlib only).

Off by default — only called when RouterConfig.cost_sync is True.
On any failure (network, parse, missing key) returns (None, None) so the
caller falls back to static config values.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
_CACHE_PATH = Path("~/.cache/anvil-serving/prices.json").expanduser()
_CACHE_TTL = 86400  # 24 h in seconds


def fetch_prices(
    model_id: str,
    cache_path: Path = _CACHE_PATH,
    ttl: int = _CACHE_TTL,
    _fetch_fn=None,
) -> tuple[Optional[float], Optional[float]]:
    """Return (input_per_mtok, output_per_mtok) USD for model_id, or (None, None).

    Caches the full LiteLLM pricing JSON at cache_path for ttl seconds so
    subsequent calls within the TTL window skip the network entirely.

    _fetch_fn is a zero-arg callable that returns raw bytes — injectable for
    tests so no real urlopen ever fires.
    """
    try:
        data = _load_data(cache_path, ttl, _fetch_fn)
        if data is None:
            return (None, None)
        entry = _find_entry(data, model_id)
        if entry is None:
            return (None, None)
        input_cost = entry.get("input_cost_per_token")
        output_cost = entry.get("output_cost_per_token")
        if input_cost is None or output_cost is None:
            return (None, None)
        return (float(input_cost) * 1e6, float(output_cost) * 1e6)
    except Exception:
        return (None, None)


def _load_data(cache_path: Path, ttl: int, _fetch_fn) -> Optional[dict]:
    if cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            if time.time() - mtime < ttl:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass  # stale or corrupt — fall through to fetch

    raw = _do_fetch(_fetch_fn)
    if raw is None:
        return None

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
    except Exception:
        pass  # non-fatal: continue with in-memory data

    return json.loads(raw)


def _do_fetch(_fetch_fn) -> bytes:
    if _fetch_fn is not None:
        return _fetch_fn()
    response = urllib.request.urlopen(_URL, timeout=10)
    return response.read()


def _find_entry(data: dict, model_id: str) -> Optional[dict]:
    if model_id in data:
        return data[model_id]
    prefixed = f"anthropic/{model_id}"
    if prefixed in data:
        return data[prefixed]
    suffix = f"/{model_id}"
    for key, val in data.items():
        if key.endswith(suffix) and isinstance(val, dict):
            return val
    return None
