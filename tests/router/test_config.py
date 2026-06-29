"""Tests for the router/tier config schema + loader (harness-router:T002).

Proves both PRD acceptance criteria:
  AC1 - configs/example.toml yields fast-local, heavy-local, cloud with their
        endpoints, dialects, and constraints.
  AC2 - every tier's auth reference NAMES an env var; no secret literal appears
        in the config and none is required to load it.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from anvil_serving.router.config import (
    ConfigError,
    RouterConfig,
    Tier,
    load,
)

# CWD-independent: example.toml lives at <repo>/configs/example.toml and this
# file is at <repo>/tests/router/test_config.py (parents[2] == repo root).
EXAMPLE = pathlib.Path(__file__).resolve().parents[2] / "configs" / "example.toml"

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Env-var names the example references; used to prove load() needs no secret.
EXAMPLE_AUTH_ENVS = (
    "ANVIL_FAST_LOCAL_KEY",
    "ANVIL_HEAVY_LOCAL_KEY",
    "ANTHROPIC_API_KEY",
)

_BASE_TIER = """\
mapping_version = "test.0"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"
"""


def _write_toml(tmp_path: pathlib.Path, body: str) -> str:
    """Dump an inline ``[router]`` config to tmp_path and return its path."""
    p = tmp_path / "cfg.toml"
    p.write_text("[router]\n" + body, encoding="utf-8")
    return str(p)


# ── AC1 ──────────────────────────────────────────────────────────────────────
def test_example_loads_expected_tiers():
    cfg = load(str(EXAMPLE))
    assert isinstance(cfg, RouterConfig)
    ids = {t.id for t in cfg.tiers}
    assert ids == {"fast-local", "heavy-local", "cloud"}
    for t in cfg.tiers:
        assert isinstance(t, Tier)
        assert t.base_url
        assert t.dialect in {"openai", "anthropic"}
        assert t.context_limit > 0
        assert t.privacy in {"local", "cloud"}
        assert isinstance(t.tool_support, bool)

    # Spot-check the worked example's concrete constraints.
    fast = cfg.tier("fast-local")
    assert fast.base_url == "http://127.0.0.1:30001/v1"
    assert fast.dialect == "openai" and fast.privacy == "local"
    assert fast.context_limit == 32768

    cloud = cfg.tier("cloud")
    assert cloud.base_url == "https://api.anthropic.com"
    assert cloud.dialect == "anthropic" and cloud.privacy == "cloud"
    assert cloud.context_limit == 200000


def test_no_localhost_in_local_endpoints():
    # Project gotcha: 127.0.0.1, never localhost (Windows IPv6 stall).
    cfg = load(str(EXAMPLE))
    for t in cfg.tiers:
        if t.privacy == "local":
            assert "localhost" not in t.base_url
            assert "127.0.0.1" in t.base_url


# ── AC2: auth refs are env-var NAMES, never secrets ───────────────────────────
def test_auth_refs_are_env_names_not_secrets(monkeypatch):
    # Every referenced env var UNSET -> load must still succeed (no secret read).
    for name in EXAMPLE_AUTH_ENVS:
        monkeypatch.delenv(name, raising=False)

    cfg = load(str(EXAMPLE))
    for t in cfg.tiers:
        assert ENV_NAME_RE.fullmatch(t.auth_env), t.auth_env


def test_example_file_has_no_secret_literals():
    text = EXAMPLE.read_text(encoding="utf-8")
    for marker in ("sk-", "ghp_", "AKIA", "xoxb-", "Bearer "):
        assert marker not in text, f"secret-looking literal {marker!r} in config"


# ── Presets + mapping version ─────────────────────────────────────────────────
def test_presets_reference_known_tiers():
    cfg = load(str(EXAMPLE))
    known = {t.id for t in cfg.tiers}
    for name, cands in cfg.presets.items():
        for cid in cands:
            assert cid in known, f"preset {name} -> unknown tier {cid}"

    assert cfg.presets["planning"] == ("cloud",)
    resolved = cfg.candidates("planning")
    assert tuple(t.id for t in resolved) == ("cloud",)
    assert resolved[0].dialect == "anthropic"


def test_candidates_preserve_order():
    cfg = load(str(EXAMPLE))
    assert tuple(t.id for t in cfg.candidates("quick-edit")) == (
        "fast-local",
        "heavy-local",
        "cloud",
    )


def test_mapping_version_present():
    cfg = load(str(EXAMPLE))
    assert isinstance(cfg.mapping_version, str)
    assert cfg.mapping_version


# ── lookup error paths ────────────────────────────────────────────────────────
def test_tier_unknown_raises():
    cfg = load(str(EXAMPLE))
    with pytest.raises(ConfigError):
        cfg.tier("nope")


def test_candidates_unknown_preset_raises():
    cfg = load(str(EXAMPLE))
    with pytest.raises(ConfigError):
        cfg.candidates("does-not-exist")


# ── validation error paths (inline TOML) ──────────────────────────────────────
def test_unknown_dialect_raises(tmp_path):
    body = _BASE_TIER.replace('dialect       = "openai"', 'dialect       = "grpc"')
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_preset_unknown_tier_raises(tmp_path):
    body = _BASE_TIER + '\n[router.presets]\nchat = ["fast-local", "ghost"]\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_duplicate_id_raises(tmp_path):
    body = _BASE_TIER + _BASE_TIER.split("\n", 1)[1]  # append the tier table again
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_missing_router_block_raises(tmp_path):
    p = tmp_path / "no_router.toml"
    p.write_text('claude_logs = "~/.claude/projects"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load(str(p))


def test_auth_env_secret_literal_rejected(tmp_path):
    # A literal secret in auth_env (lowercase / non-env-name) must be rejected.
    body = _BASE_TIER.replace(
        'auth_env      = "ANVIL_FAST_LOCAL_KEY"',
        'auth_env      = "sk-test-abc123"',
    )
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_aws_key_id_shaped_auth_env_rejected(tmp_path):
    # An AWS access key id is all-caps alphanumeric and so fits the env-name
    # charset; it must still be rejected as a credential-shaped literal.
    body = _BASE_TIER.replace(
        'auth_env      = "ANVIL_FAST_LOCAL_KEY"',
        'auth_env      = "AKIAIOSFODNN7EXAMPLE"',
    )
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_missing_mapping_version_raises(tmp_path):
    body = _BASE_TIER.replace('mapping_version = "test.0"\n', "")
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_non_positive_context_limit_raises(tmp_path):
    body = _BASE_TIER.replace("context_limit = 32768", "context_limit = 0")
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── regression: legacy loader must not choke on the new [router] block ─────────
def test_legacy_loader_still_works():
    from anvil_serving.config import load as legacy_load

    cfg = legacy_load(str(EXAMPLE))
    assert cfg["claude_logs"]
    assert "router" in cfg
