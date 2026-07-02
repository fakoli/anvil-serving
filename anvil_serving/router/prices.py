"""Optional cost-sync: fetch per-model pricing from the free LiteLLM JSON (stdlib only).

Off by default — only called when RouterConfig.cost_sync is True.
On any failure (network, parse, missing key) returns (None, None) so the
caller falls back to static config values.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

# Per-process memo of the parsed pricing table, keyed by cache path: config
# load calls fetch_prices once PER TIER, and without the memo an offline box
# with N tiers pays N sequential network timeouts at startup.
_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()

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
    # Per-process memo first: one parse (and at most one fetch) per process.
    with _MEMO_LOCK:
        memo = _MEMO.get(cache_path)
    if memo is not None:
        return memo

    data: Optional[dict] = None
    if cache_path.exists():
        try:
            mtime = cache_path.stat().st_mtime
            if time.time() - mtime < ttl:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass  # stale or corrupt — fall through to fetch

    if data is None:
        try:
            raw = _do_fetch(_fetch_fn)
        except Exception:
            raw = None
        if raw is not None:
            # Parse BEFORE caching: a 200 that isn't the pricing JSON (proxy
            # error page, captive portal) must never poison the disk cache.
            data = json.loads(raw)
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace so a concurrent process can never read a
                # torn/partial cache file.
                fd, tmp = tempfile.mkstemp(dir=str(cache_path.parent))
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(raw)
                    os.replace(tmp, str(cache_path))
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise
            except Exception:
                pass  # non-fatal: continue with in-memory data
        elif cache_path.exists():
            # Network fetch failed but a (possibly stale) cache exists: a
            # stale price table beats no prices at all.
            data = json.loads(cache_path.read_text(encoding="utf-8"))

    if data is not None:
        with _MEMO_LOCK:
            _MEMO[cache_path] = data
    return data


def _do_fetch(_fetch_fn) -> bytes:
    if _fetch_fn is not None:
        return _fetch_fn()
    with urllib.request.urlopen(_URL, timeout=10) as response:
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
