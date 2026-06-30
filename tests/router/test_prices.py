"""Tests for optional cost-sync (advise-and-defer:T006).

Never touches the network: all fetches are intercepted via _fetch_fn or
monkeypatching. Fixture pricing is written to tmp files.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

import anvil_serving.router.config as config_mod
from anvil_serving.router.config import load
from anvil_serving.router.prices import fetch_prices

# ── fixture data ──────────────────────────────────────────────────────────────
_FIXTURE_MODEL = "claude-test-model"
_FIXTURE_PRICES = {
    _FIXTURE_MODEL: {
        "input_cost_per_token": 0.000003,   # $3 / million tokens
        "output_cost_per_token": 0.000015,  # $15 / million tokens
        "litellm_provider": "anthropic",
    }
}
_FIXTURE_JSON = json.dumps(_FIXTURE_PRICES).encode()


# ── helpers ───────────────────────────────────────────────────────────────────
def _write_toml(
    tmp_path: pathlib.Path,
    *,
    cost_sync: bool,
    add_costs: bool = False,
) -> pathlib.Path:
    cost_sync_str = "true" if cost_sync else "false"
    costs = (
        "cost_input_per_mtok  = 3.0\ncost_output_per_mtok = 15.0\n" if add_costs else ""
    )
    content = f"""\
[router]
mapping_version = "test.0"
cost_sync = {cost_sync_str}

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"
{costs}
[router.presets]
chat = ["fast-local"]
"""
    p = tmp_path / "cfg.toml"
    p.write_text(content, encoding="utf-8")
    return p


# ── unit tests: fetch_prices directly ────────────────────────────────────────

def test_fetch_failure_graceful(tmp_path):
    """Any exception from _fetch_fn → (None, None), no crash."""

    def bad_fetch():
        raise OSError("simulated network failure")

    result = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=tmp_path / "prices.json",
        _fetch_fn=bad_fetch,
    )
    assert result == (None, None)


def test_fill_unset_cost_from_fixture(tmp_path):
    """Fixture pricing JSON → correct per-Mtok conversion (0.000003 → 3.0)."""

    def good_fetch():
        return _FIXTURE_JSON

    inp, out = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=tmp_path / "prices.json",
        _fetch_fn=good_fetch,
    )
    assert inp == pytest.approx(3.0)   # 0.000003 * 1e6
    assert out == pytest.approx(15.0)  # 0.000015 * 1e6


def test_cache_reuse(tmp_path):
    """A fresh cache file is used without calling _fetch_fn."""
    cache = tmp_path / "prices.json"
    cache.write_bytes(_FIXTURE_JSON)
    now = time.time()
    os.utime(cache, (now, now))

    fetch_called: list[bool] = []

    def should_not_be_called():
        fetch_called.append(True)
        return _FIXTURE_JSON

    inp, out = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=cache,
        ttl=3600,
        _fetch_fn=should_not_be_called,
    )
    assert not fetch_called, "_fetch_fn was called despite a fresh cache"
    assert inp == pytest.approx(3.0)
    assert out == pytest.approx(15.0)


def test_stale_cache_refetch(tmp_path):
    """A stale cache triggers re-fetch via _fetch_fn."""
    cache = tmp_path / "prices.json"
    cache.write_bytes(_FIXTURE_JSON)
    stale_time = time.time() - 7200  # 2 h ago
    os.utime(cache, (stale_time, stale_time))

    fetch_called: list[bool] = []

    def fresh_fetch():
        fetch_called.append(True)
        return _FIXTURE_JSON

    inp, out = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=cache,
        ttl=3600,
        _fetch_fn=fresh_fetch,
    )
    assert fetch_called, "_fetch_fn was not called despite stale cache"
    assert inp == pytest.approx(3.0)


def test_missing_model_returns_none(tmp_path):
    """A model_id not in the pricing JSON → (None, None)."""

    def fetch_fn():
        return _FIXTURE_JSON

    inp, out = fetch_prices(
        "nonexistent-model",
        cache_path=tmp_path / "prices.json",
        _fetch_fn=fetch_fn,
    )
    assert (inp, out) == (None, None)


def test_anthropic_prefix_fallback(tmp_path):
    """Model not found bare → 'anthropic/{model_id}' prefix is tried next."""
    pricing = {
        f"anthropic/{_FIXTURE_MODEL}": {
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000002,
        }
    }

    def fetch_fn():
        return json.dumps(pricing).encode()

    inp, out = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=tmp_path / "prices.json",
        _fetch_fn=fetch_fn,
    )
    assert inp == pytest.approx(1.0)
    assert out == pytest.approx(2.0)


def test_suffix_fallback(tmp_path):
    """Model not found bare or prefixed → any key ending '/{model_id}' is tried."""
    pricing = {
        f"openai/{_FIXTURE_MODEL}": {
            "input_cost_per_token": 0.000005,
            "output_cost_per_token": 0.000010,
        }
    }

    def fetch_fn():
        return json.dumps(pricing).encode()

    inp, out = fetch_prices(
        _FIXTURE_MODEL,
        cache_path=tmp_path / "prices.json",
        _fetch_fn=fetch_fn,
    )
    assert inp == pytest.approx(5.0)
    assert out == pytest.approx(10.0)


# ── config-level cost_sync behavior ──────────────────────────────────────────

def test_cost_sync_default_is_false():
    """cost_sync defaults to False — no-network default guaranteed by RouterConfig."""
    from anvil_serving.router.config import RouterConfig, Tier
    from types import MappingProxyType

    tier = Tier(
        id="t",
        base_url="http://127.0.0.1:1/v1",
        dialect="openai",
        context_limit=1024,
        privacy="local",
        tool_support=True,
        auth_env="K",
    )
    cfg = RouterConfig(
        tiers=(tier,),
        presets=MappingProxyType({}),
        mapping_version="x",
    )
    assert cfg.cost_sync is False


def test_no_fetch_when_cost_sync_false(monkeypatch, tmp_path):
    """cost_sync=False (default) → fetch_prices never called; tier costs stay None."""

    def _never_called(model_id, **kwargs):
        raise AssertionError("fetch_prices must not be called when cost_sync=False")

    monkeypatch.setattr(config_mod, "fetch_prices", _never_called)

    cfg = load(str(_write_toml(tmp_path, cost_sync=False)))
    assert all(t.cost_input_per_mtok is None for t in cfg.tiers)
    assert all(t.cost_output_per_mtok is None for t in cfg.tiers)


def test_cost_sync_fills_unset_tier_costs(monkeypatch, tmp_path):
    """cost_sync=True + tier with no cost fields → filled from prices."""

    def fake_prices(model_id, **kwargs):
        return (3.0, 15.0)

    monkeypatch.setattr(config_mod, "fetch_prices", fake_prices)

    cfg = load(str(_write_toml(tmp_path, cost_sync=True, add_costs=False)))
    t = cfg.tiers[0]
    assert t.cost_input_per_mtok == pytest.approx(3.0)
    assert t.cost_output_per_mtok == pytest.approx(15.0)


def test_static_cost_not_overwritten(monkeypatch, tmp_path):
    """cost_sync=True + explicit static cost → static value is NOT overwritten."""

    def fake_prices(model_id, **kwargs):
        return (999.0, 999.0)

    monkeypatch.setattr(config_mod, "fetch_prices", fake_prices)

    cfg = load(str(_write_toml(tmp_path, cost_sync=True, add_costs=True)))
    t = cfg.tiers[0]
    assert t.cost_input_per_mtok == pytest.approx(3.0)
    assert t.cost_output_per_mtok == pytest.approx(15.0)


def test_cost_sync_parse_error_on_non_bool(tmp_path):
    """cost_sync set to a non-boolean string raises ConfigError."""
    from anvil_serving.router.config import ConfigError

    content = """\
[router]
mapping_version = "test.0"
cost_sync = "yes"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[router.presets]
chat = ["fast-local"]
"""
    p = tmp_path / "bad.toml"
    p.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match="cost_sync"):
        load(str(p))
