"""Tests for the router/tier config schema + loader (harness-router:T002).

Proves both PRD acceptance criteria:
  AC1 - configs/example.toml yields fast-local and heavy-local (local-only,
        ADR-0001 / advise-and-defer:T001) with their endpoints, dialects, and
        constraints.  The cloud tier lives in example-with-cloud.toml (opt-in).
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
_CONFIGS = pathlib.Path(__file__).resolve().parents[2] / "configs"
EXAMPLE = _CONFIGS / "example.toml"
EXAMPLE_WITH_CLOUD = _CONFIGS / "example-with-cloud.toml"

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Env-var names the local-only example references; used to prove load() needs
# no cloud secret.  ANTHROPIC_API_KEY is intentionally absent (ADR-0001).
EXAMPLE_AUTH_ENVS = (
    "ANVIL_FAST_LOCAL_KEY",
    "ANVIL_HEAVY_LOCAL_KEY",
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
    """example.toml is local-only (ADR-0001 / T001): only local tiers present."""
    cfg = load(str(EXAMPLE))
    assert isinstance(cfg, RouterConfig)
    ids = {t.id for t in cfg.tiers}
    # Local-only: no cloud tier in the default config.
    assert ids == {"fast-local", "heavy-local"}
    for t in cfg.tiers:
        assert isinstance(t, Tier)
        assert t.base_url
        assert t.dialect == "openai"        # both local tiers speak OpenAI dialect
        assert t.context_limit > 0
        assert t.privacy == "local"         # every tier in the default config is local
        assert isinstance(t.tool_support, bool)

    # Spot-check the worked example's concrete constraints.
    fast = cfg.tier("fast-local")
    assert fast.base_url == "http://127.0.0.1:30001/v1"
    assert fast.dialect == "openai" and fast.privacy == "local"
    assert fast.context_limit == 32768


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

    # Local-only: planning routes to the best local tier, not cloud (T001).
    assert cfg.presets["planning"] == ("heavy-local",)
    resolved = cfg.candidates("planning")
    assert tuple(t.id for t in resolved) == ("heavy-local",)
    assert resolved[0].dialect == "openai"  # local tiers use OpenAI dialect


def test_candidates_preserve_order():
    cfg = load(str(EXAMPLE))
    # Local-only: quick-edit routes fast -> heavy, no cloud (T001).
    assert tuple(t.id for t in cfg.candidates("quick-edit")) == (
        "fast-local",
        "heavy-local",
    )


def test_mapping_version_present():
    cfg = load(str(EXAMPLE))
    assert isinstance(cfg.mapping_version, str)
    assert cfg.mapping_version
    assert cfg.mapping_version == "2026-06-30.0"


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


# ── regression: type guards, I/O, presets, URL scheme, immutability ───────────
def test_non_string_dialect_raises(tmp_path):
    # An unhashable TOML value (array) must not blow up set-membership.
    body = _BASE_TIER.replace(
        'dialect       = "openai"', 'dialect       = ["openai"]'
    )
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_non_string_privacy_raises(tmp_path):
    body = _BASE_TIER.replace('privacy       = "local"', 'privacy       = ["local"]')
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_missing_file_raises_configerror():
    with pytest.raises(ConfigError):
        load("does/not/exist.toml")


def test_malformed_toml_raises_configerror(tmp_path):
    p = tmp_path / "broken.toml"
    p.write_text("[router]\nthis is = = broken", encoding="utf-8")
    with pytest.raises(ConfigError):
        load(str(p))


def test_empty_preset_raises(tmp_path):
    body = _BASE_TIER + '\n[router.presets]\nchat = []\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_duplicate_candidate_in_preset_raises(tmp_path):
    body = _BASE_TIER + '\n[router.presets]\nchat = ["fast-local", "fast-local"]\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_base_url_without_scheme_raises(tmp_path):
    body = _BASE_TIER.replace(
        'base_url      = "http://127.0.0.1:30001/v1"',
        'base_url      = "127.0.0.1:30001/v1"',
    )
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── Fix 2: http(s)-only base_url (chore/harden-exposure) ─────────────────────
@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "file:///C:/Windows/System32/drivers/etc/hosts",
        "ftp://internal.corp/secret",
        "gopher://169.254.169.254/",
        "data:text/plain,hello",
        "javascript:alert(1)",
    ],
)
def test_non_http_scheme_base_url_raises(tmp_path, bad_url):
    """Non-http(s) schemes must be rejected at config load time (SSRF / local-file)."""
    body = _BASE_TIER.replace(
        'base_url      = "http://127.0.0.1:30001/v1"',
        f'base_url      = "{bad_url}"',
    )
    with pytest.raises(ConfigError, match="http"):
        load(_write_toml(tmp_path, body))


@pytest.mark.parametrize(
    "good_url",
    [
        "http://127.0.0.1:30001/v1",
        "https://api.anthropic.com",
        "https://api.openai.com/v1",
        "HTTP://127.0.0.1:8080",   # case-insensitive
        "HTTPS://EXAMPLE.COM",
    ],
)
def test_http_and_https_base_url_accepted(tmp_path, good_url):
    """http:// and https:// schemes (case-insensitive) must load without error."""
    body = _BASE_TIER.replace(
        'base_url      = "http://127.0.0.1:30001/v1"',
        f'base_url      = "{good_url}"',
    )
    cfg = load(_write_toml(tmp_path, body))
    assert cfg.tiers[0].base_url == good_url


def test_config_is_hashable_and_presets_immutable():
    cfg = load(str(EXAMPLE))
    # The (unhashable) presets mapping is excluded from __hash__; this must work.
    assert hash(cfg) is not None
    # MappingProxyType blocks silent mutation of the preset map.
    with pytest.raises(TypeError):
        cfg.presets["x"] = 1


# ── regression: legacy loader must not choke on the new [router] block ─────────
def test_legacy_loader_still_works():
    from anvil_serving.config import load as legacy_load

    cfg = legacy_load(str(EXAMPLE))
    assert cfg["claude_logs"]
    assert "router" in cfg


# ── T001: local-only default config (ADR-0001 / advise-and-defer) ─────────────
def test_example_has_only_local_tiers():
    """AC: configs/example.toml is the shipped default and must hold ZERO cloud
    tiers (ADR-0001).  Every tier it declares must have privacy == 'local'."""
    cfg = load(str(EXAMPLE))
    for t in cfg.tiers:
        assert t.privacy == "local", (
            f"tier {t.id!r} has privacy={t.privacy!r}; "
            f"the default config must be local-only (T001)"
        )


def test_example_loads_without_cloud_env_var(monkeypatch):
    """AC: configs/example.toml loads successfully when ANTHROPIC_API_KEY is
    absent (and all other cloud env vars).  The default config requires NO
    cloud credential at load time or at runtime."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANVIL_FAST_LOCAL_KEY", raising=False)
    monkeypatch.delenv("ANVIL_HEAVY_LOCAL_KEY", raising=False)
    # Must not raise regardless of what env vars are set.
    cfg = load(str(EXAMPLE))
    assert cfg is not None
    # No cloud tier loaded.
    assert all(t.privacy == "local" for t in cfg.tiers)


def test_example_with_cloud_has_cloud_tier():
    """Companion: configs/example-with-cloud.toml (the opt-in file) DOES declare
    a cloud tier so operators have a worked example for opting in."""
    cfg = load(str(EXAMPLE_WITH_CLOUD))
    cloud_tiers = [t for t in cfg.tiers if t.privacy == "cloud"]
    assert cloud_tiers, "example-with-cloud.toml must contain at least one cloud tier"
    cloud = cloud_tiers[0]
    assert cloud.auth_env == "ANTHROPIC_API_KEY"
    assert cloud.base_url == "https://api.anthropic.com"


def test_example_with_cloud_loads_without_key(monkeypatch):
    """example-with-cloud.toml loads (config parse) even when ANTHROPIC_API_KEY
    is absent — load() never reads secrets, only records auth_env names."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = load(str(EXAMPLE_WITH_CLOUD))
    # The cloud tier is declared in the file; load() should succeed.
    assert any(t.privacy == "cloud" for t in cfg.tiers)


def test_example_has_no_cloud_tier_id():
    """example.toml must not declare any tier with id 'cloud' (belt-and-suspenders
    check separate from privacy field)."""
    cfg = load(str(EXAMPLE))
    assert "cloud" not in {t.id for t in cfg.tiers}, (
        "tier id 'cloud' found in the default config; "
        "move the cloud tier to example-with-cloud.toml (T001)"
    )
