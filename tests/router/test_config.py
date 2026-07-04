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
    ServerConfig,
    Tier,
    load,
    load_server_config,
)

# CWD-independent: example.toml lives at <repo>/configs/example.toml and this
# file is at <repo>/tests/router/test_config.py (parents[2] == repo root).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONFIGS = _REPO_ROOT / "configs"
EXAMPLE = _CONFIGS / "example.toml"
EXAMPLE_WITH_CLOUD = _CONFIGS / "example-with-cloud.toml"
EXAMPLE_FLEXIBILITY = _CONFIGS / "example-flexibility.toml"

# flexibility:T011 — the fakoli-dark two-mode split (ADR-0011).
_FAKOLI = _REPO_ROOT / "examples" / "fakoli-dark"
FAKOLI_LIVE = _FAKOLI / "anvil-router.live.toml"
FAKOLI_AGENTIC = _FAKOLI / "anvil-router.agentic.toml"
FAKOLI_FLEXIBILITY = _FAKOLI / "anvil-router.flexibility.toml"

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


# ── genericity:R001 — a tier needs `model` or the preset token 404s upstream ──
def test_example_local_tiers_model_field():
    """Shipped configs set the `model` field on every local tier, so the router forwards
    the served-model-name upstream (not the routing token) and does not 404 out of the box."""
    for cfg_path in (EXAMPLE, EXAMPLE_WITH_CLOUD):
        cfg = load(str(cfg_path))
        for t in cfg.tiers:
            if t.privacy == "local":
                assert t.model, f"{cfg_path.name}: local tier {t.id!r} is missing model="


def test_cloud_tier_has_model():
    """The opt-in cloud tier also sets `model` (else the preset token 400/404s upstream)."""
    cfg = load(str(EXAMPLE_WITH_CLOUD))
    cloud = [t for t in cfg.tiers if t.privacy == "cloud"]
    assert cloud, "example-with-cloud.toml should declare a cloud tier"
    for t in cloud:
        assert t.model, f"cloud tier {t.id!r} is missing model="


def test_missing_model_warning(tmp_path, capsys):
    """A local tier without `model=` still loads (non-fatal) but warns, naming the tier."""
    cfg_path = _write_toml(tmp_path, _BASE_TIER)  # fast-local, no model=
    load(cfg_path)
    err = capsys.readouterr().err
    assert "fast-local" in err
    assert "model" in err.lower()


def test_local_tier_with_model_does_not_warn(tmp_path, capsys):
    """Setting `model=` on a local tier silences the R001 warning."""
    body = _BASE_TIER + 'model         = "gpt-oss-20b"\n'
    cfg_path = _write_toml(tmp_path, body)
    load(cfg_path)
    err = capsys.readouterr().err
    assert "fast-local" not in err


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


# ── T002: metered_cloud config parsing (ADR-0001 / advise-and-defer:T002) ────
#
# These tests write full TOML bodies (not using _write_toml + _BASE_TIER) because
# metered_cloud is a [router]-level key that must appear BEFORE [[router.tiers]]
# arrays in TOML (keys cannot be added to a table after an array-table opener).

_MC_TOML_HEADER = """\
[router]
mapping_version = "test.mc"
"""

_MC_TOML_TIER = """\

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


def _write_mc_toml(tmp_path: pathlib.Path, extra_router_keys: str = "") -> str:
    """Write a minimal router config with optional extra [router]-level keys."""
    p = tmp_path / "mc.toml"
    p.write_text(_MC_TOML_HEADER + extra_router_keys + _MC_TOML_TIER, encoding="utf-8")
    return str(p)


def test_metered_cloud_absent_defaults_to_empty(tmp_path):
    """A config with no metered_cloud key → RouterConfig.metered_cloud == ().
    Absent == empty: cloud is never a candidate by default (ADR-0001)."""
    cfg = load(_write_mc_toml(tmp_path))
    assert cfg.metered_cloud == ()


def test_metered_cloud_present_parses_to_tuple(tmp_path):
    """metered_cloud = ["planning", "chat"] → tuple of strings in that order."""
    cfg = load(_write_mc_toml(tmp_path, 'metered_cloud = ["planning", "chat"]\n'))
    assert cfg.metered_cloud == ("planning", "chat")


def test_metered_cloud_empty_list_parses_to_empty_tuple(tmp_path):
    """metered_cloud = [] → (): explicitly empty maps to the same result as absent."""
    cfg = load(_write_mc_toml(tmp_path, "metered_cloud = []\n"))
    assert cfg.metered_cloud == ()


def test_metered_cloud_non_list_raises(tmp_path):
    """metered_cloud must be a list; a scalar value raises ConfigError."""
    with pytest.raises(ConfigError):
        load(_write_mc_toml(tmp_path, 'metered_cloud = "planning"\n'))


def test_metered_cloud_non_string_elements_raises(tmp_path):
    """metered_cloud must be a list of strings; non-string elements raise ConfigError."""
    with pytest.raises(ConfigError):
        load(_write_mc_toml(tmp_path, "metered_cloud = [1, 2]\n"))


def test_default_config_metered_cloud_is_empty():
    """example.toml (local-only default) has metered_cloud == () — the local-only
    config has no cloud tier and no metered mapping (ADR-0001 / T002)."""
    cfg = load(str(EXAMPLE))
    assert cfg.metered_cloud == ()


def test_example_with_cloud_has_metered_cloud_set():
    """example-with-cloud.toml must have a non-empty metered_cloud showing operators
    how to opt specific work-classes into metered cloud routing (T002 worked example)."""
    cfg = load(str(EXAMPLE_WITH_CLOUD))
    assert isinstance(cfg.metered_cloud, tuple)
    assert len(cfg.metered_cloud) > 0, (
        "example-with-cloud.toml must set metered_cloud to at least one work-class "
        "(T002 worked example — nothing is metered unless mapped)"
    )
    # planning is the canonical example in the worked example (ADR-0001)
    assert "planning" in cfg.metered_cloud


# ── T003: cost_input_per_mtok / cost_output_per_mtok (ADR-0001 / advise-and-defer:T003) ──
def test_cost_fields_absent_defaults_to_none(tmp_path):
    """Cost fields absent on a tier → None (no metered billing; e.g. all local tiers)."""
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    t = cfg.tiers[0]
    assert t.cost_input_per_mtok is None
    assert t.cost_output_per_mtok is None


def test_cost_fields_parse_when_present(tmp_path):
    """cost_input_per_mtok / cost_output_per_mtok parse as floats when set."""
    body = _BASE_TIER + "cost_input_per_mtok  = 3.0\ncost_output_per_mtok = 15.0\n"
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert t.cost_input_per_mtok == pytest.approx(3.0)
    assert t.cost_output_per_mtok == pytest.approx(15.0)


def test_cost_fields_accept_integer_values(tmp_path):
    """Integer TOML values (e.g. 3 instead of 3.0) are accepted and cast to float."""
    body = _BASE_TIER + "cost_input_per_mtok  = 3\ncost_output_per_mtok = 15\n"
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert isinstance(t.cost_input_per_mtok, float)
    assert t.cost_input_per_mtok == pytest.approx(3.0)


def test_cost_fields_accept_zero(tmp_path):
    """cost_input_per_mtok = 0.0 is valid (free tier / internal endpoint)."""
    body = _BASE_TIER + "cost_input_per_mtok = 0.0\ncost_output_per_mtok = 0.0\n"
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert t.cost_input_per_mtok == pytest.approx(0.0)
    assert t.cost_output_per_mtok == pytest.approx(0.0)


def test_cost_input_non_number_raises(tmp_path):
    """A non-numeric cost_input_per_mtok must raise ConfigError."""
    body = _BASE_TIER + 'cost_input_per_mtok = "expensive"\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_cost_output_non_number_raises(tmp_path):
    """A non-numeric cost_output_per_mtok must raise ConfigError."""
    body = _BASE_TIER + "cost_output_per_mtok = true\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_cost_input_negative_raises(tmp_path):
    """A negative cost_input_per_mtok must raise ConfigError."""
    body = _BASE_TIER + "cost_input_per_mtok = -1.0\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_cost_output_negative_raises(tmp_path):
    """A negative cost_output_per_mtok must raise ConfigError."""
    body = _BASE_TIER + "cost_output_per_mtok = -0.01\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_local_tiers_in_example_have_no_cost_fields():
    """Local tiers in the default config carry no cost fields (cost_* == None)."""
    cfg = load(str(EXAMPLE))
    for t in cfg.tiers:
        assert t.cost_input_per_mtok is None, f"tier {t.id!r} unexpectedly has cost_input_per_mtok"
        assert t.cost_output_per_mtok is None, f"tier {t.id!r} unexpectedly has cost_output_per_mtok"


def test_example_with_cloud_has_cost_fields_on_cloud_tier():
    """example-with-cloud.toml (T003 worked example) sets cost fields on the cloud tier."""
    cfg = load(str(EXAMPLE_WITH_CLOUD))
    cloud = next(t for t in cfg.tiers if t.privacy == "cloud")
    assert cloud.cost_input_per_mtok is not None, (
        "cloud tier in example-with-cloud.toml must set cost_input_per_mtok (T003 worked example)"
    )
    assert cloud.cost_output_per_mtok is not None, (
        "cloud tier in example-with-cloud.toml must set cost_output_per_mtok (T003 worked example)"
    )
    assert cloud.cost_input_per_mtok > 0
    assert cloud.cost_output_per_mtok > 0


# ── genericity:T005 — [router].relay_timeout ────────────────────────────────
def test_relay_timeout_defaults_to_20(tmp_path):
    """Absent relay_timeout -> a short (20s) default, not the 120s cloud default."""
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    assert cfg.relay_timeout == pytest.approx(20.0)


def test_relay_timeout_parses_when_set(tmp_path):
    body = "relay_timeout = 5\n" + _BASE_TIER
    cfg = load(_write_toml(tmp_path, body))
    assert cfg.relay_timeout == pytest.approx(5.0)
    assert isinstance(cfg.relay_timeout, float)


def test_relay_timeout_accepts_float(tmp_path):
    body = "relay_timeout = 2.5\n" + _BASE_TIER
    cfg = load(_write_toml(tmp_path, body))
    assert cfg.relay_timeout == pytest.approx(2.5)


def test_relay_timeout_zero_raises(tmp_path):
    body = "relay_timeout = 0\n" + _BASE_TIER
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_relay_timeout_negative_raises(tmp_path):
    body = "relay_timeout = -1\n" + _BASE_TIER
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_relay_timeout_non_number_raises(tmp_path):
    body = 'relay_timeout = "fast"\n' + _BASE_TIER
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_relay_timeout_bool_raises(tmp_path):
    """bool is an int subclass in Python; must be rejected explicitly."""
    body = "relay_timeout = true\n" + _BASE_TIER
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── genericity:T004 — [router].verify_local_min ─────────────────────────────
def test_verify_local_min_defaults_to_true(tmp_path):
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    assert cfg.verify_local_min is True


def test_verify_local_min_can_be_disabled(tmp_path):
    body = "verify_local_min = false\n" + _BASE_TIER
    cfg = load(_write_toml(tmp_path, body))
    assert cfg.verify_local_min is False


def test_verify_local_min_non_bool_raises(tmp_path):
    body = 'verify_local_min = "yes"\n' + _BASE_TIER
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── genericity:T003 — per-tier extra_body ───────────────────────────────────
def test_extra_body_absent_defaults_to_none(tmp_path):
    """extra_body absent -> None (no regression: body is unchanged from today)."""
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    assert cfg.tiers[0].extra_body is None


def test_extra_body_parses_inline_table(tmp_path):
    # TOML's `[router.tiers.X]` sub-table syntax only unambiguously targets the
    # LAST entry of an array-of-tables, so use the explicit inline-table form
    # here instead -- it stays correct regardless of how many tiers precede it.
    body = _BASE_TIER + (
        'extra_body = { temperature_scale = 0.9, '
        'chat_template_kwargs = { enable_thinking = false } }\n'
    )
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert t.extra_body == {
        "temperature_scale": 0.9,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_extra_body_non_dict_raises(tmp_path):
    body = _BASE_TIER + "extra_body = [1, 2, 3]\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_extra_body_non_string_key_type_is_toml_illegal(tmp_path):
    """TOML tables always have string keys, so the JSON-serialisability guard is
    exercised via a value TOML cannot represent instead (there is no TOML
    non-serialisable-but-valid shape for a *value*, so this pins that a bad
    table is still caught structurally): a non-dict value under extra_body."""
    body = _BASE_TIER + 'extra_body = "not-a-table"\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── flexibility:T007 — additive Tier engine/quantization/params/timeout ─────
def test_t007_fields_absent_default_to_none(tmp_path):
    """AC1 — none of the new fields is required; a config declaring NONE of them
    parses and all four read as None (existing configs are unaffected)."""
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    t = cfg.tiers[0]
    assert t.engine is None
    assert t.quantization is None
    assert t.params is None
    assert t.timeout is None


def test_t007_fields_round_trip(tmp_path):
    """AC (b) — a tier declaring engine/quantization/params/timeout round-trips
    each value with the expected type."""
    body = _BASE_TIER + (
        'engine        = "vllm"\n'
        'quantization  = "nvfp4"\n'
        'timeout       = 120\n'
        'params        = { tensor_parallel = 2, kv_cache_dtype = "fp8" }\n'
    )
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert t.engine == "vllm"
    assert t.quantization == "nvfp4"
    assert t.timeout == pytest.approx(120.0)
    assert isinstance(t.timeout, float)
    assert t.params == {"tensor_parallel": 2, "kv_cache_dtype": "fp8"}


def test_t007_shipped_configs_parse_with_none_fields():
    """AC1 — every shipped example config parses unchanged and sets none of the
    new fields (they read as None on every tier): the change is a no-op for
    existing configs. example-flexibility.toml is the one deliberate exception —
    it is the worked example that USES these fields (flexibility:T010) — so it is
    excluded here and covered by its own test below."""
    for cfg_path in sorted(_CONFIGS.glob("*.toml")):
        if cfg_path.name == "example-flexibility.toml":
            continue
        if cfg_path.name.startswith("modes"):
            continue  # a [modes] mode-manifest (ADR-0011), not a [router] config
        if cfg_path.name == "serve-recipes.toml":
            continue  # a serve-recipe registry ([[recipe]] rows), not a [router] config
        cfg = load(str(cfg_path))
        for t in cfg.tiers:
            assert t.engine is None, f"{cfg_path.name}: {t.id!r} engine"
            assert t.quantization is None, f"{cfg_path.name}: {t.id!r} quantization"
            assert t.params is None, f"{cfg_path.name}: {t.id!r} params"
            assert t.timeout is None, f"{cfg_path.name}: {t.id!r} timeout"


def test_t007_engine_non_string_raises(tmp_path):
    body = _BASE_TIER + "engine = 42\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_quantization_non_string_raises(tmp_path):
    body = _BASE_TIER + "quantization = true\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_params_non_table_raises(tmp_path):
    body = _BASE_TIER + "params = [1, 2, 3]\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_timeout_zero_raises(tmp_path):
    body = _BASE_TIER + "timeout = 0\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_timeout_negative_raises(tmp_path):
    body = _BASE_TIER + "timeout = -5\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_timeout_bool_raises(tmp_path):
    """bool is an int subclass; reject it explicitly like relay_timeout does."""
    body = _BASE_TIER + "timeout = true\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t007_timeout_non_number_raises(tmp_path):
    body = _BASE_TIER + 'timeout = "fast"\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── flexibility:T009 — optional per-tier Tier.max_concurrency (ADR-0010 P3) ──
def test_t009_max_concurrency_absent_defaults_to_none(tmp_path):
    """AC (absent) — max_concurrency is optional; a config omitting it parses and
    the field reads as None (only the process-global limiter applies)."""
    cfg = load(_write_toml(tmp_path, _BASE_TIER))
    assert cfg.tiers[0].max_concurrency is None


def test_t009_max_concurrency_round_trips_positive_int(tmp_path):
    """A tier declaring a positive-int max_concurrency round-trips it as an int."""
    body = _BASE_TIER + "max_concurrency = 4\n"
    cfg = load(_write_toml(tmp_path, body))
    t = cfg.tiers[0]
    assert t.max_concurrency == 4
    assert isinstance(t.max_concurrency, int) and not isinstance(t.max_concurrency, bool)


def test_t009_max_concurrency_is_not_a_required_key(tmp_path):
    """max_concurrency is NOT in _REQUIRED_TIER_KEYS: a tier without it still
    loads (proven by the base tier parsing above); this pins the intent that the
    field never becomes mandatory."""
    from anvil_serving.router.config import _REQUIRED_TIER_KEYS
    assert "max_concurrency" not in _REQUIRED_TIER_KEYS


def test_t009_shipped_configs_parse_with_none_max_concurrency():
    """Every shipped example config parses unchanged and sets max_concurrency on
    no tier (it reads as None everywhere): the change is a no-op for existing
    configs."""
    for cfg_path in sorted(_CONFIGS.glob("*.toml")):
        if cfg_path.name.startswith("modes"):
            continue  # a [modes] mode-manifest (ADR-0011), not a [router] config
        if cfg_path.name == "serve-recipes.toml":
            continue  # a serve-recipe registry ([[recipe]] rows), not a [router] config
        cfg = load(str(cfg_path))
        for t in cfg.tiers:
            assert t.max_concurrency is None, f"{cfg_path.name}: {t.id!r} max_concurrency"


def test_t009_max_concurrency_zero_raises(tmp_path):
    body = _BASE_TIER + "max_concurrency = 0\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t009_max_concurrency_negative_raises(tmp_path):
    body = _BASE_TIER + "max_concurrency = -2\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t009_max_concurrency_bool_raises(tmp_path):
    """bool is an int subclass; reject it explicitly like timeout does."""
    body = _BASE_TIER + "max_concurrency = true\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t009_max_concurrency_float_raises(tmp_path):
    """A concurrency cap is a count: a float is not a valid slot count."""
    body = _BASE_TIER + "max_concurrency = 2.5\n"
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


def test_t009_max_concurrency_string_raises(tmp_path):
    body = _BASE_TIER + 'max_concurrency = "two"\n'
    with pytest.raises(ConfigError):
        load(_write_toml(tmp_path, body))


# ── flexibility:T010 — example-flexibility.toml (specialized-engine tier) ────
#
# ADR-0010: a config that points anvil at an EXTERNAL OpenAI-compatible engine
# (gpt-oss-120b via your own vLLM). The specialized tier is a plain
# privacy="local" tier — served by the EXISTING RelayBackend, no new backend
# class — that additionally carries flexibility-mode knobs (engine tag, per-tier
# timeout override, extra_body reasoning knobs).
def test_flexibility_config_parses_with_engine_and_timeout():
    """AC — configs/example-flexibility.toml parses and the specialized tier has
    its `engine` and `timeout` fields populated (the flexibility-mode knobs)."""
    cfg = load(str(EXAMPLE_FLEXIBILITY))
    assert isinstance(cfg, RouterConfig)
    tier = cfg.tier("specialist-vllm")
    # The two fields the task pins explicitly.
    assert tier.engine == "vllm"
    assert tier.timeout == pytest.approx(90.0)
    assert isinstance(tier.timeout, float)
    # It's a local specialized engine: OpenAI dialect, 127.0.0.1 (never localhost),
    # a served-model-name set, and the reasoning knob forwarded via extra_body.
    assert tier.privacy == "local"
    assert tier.dialect == "openai"
    assert tier.model == "gpt-oss-120b"
    assert "127.0.0.1" in tier.base_url and "localhost" not in tier.base_url
    assert tier.extra_body == {"reasoning_effort": "high"}


def test_flexibility_specialized_tier_builds_a_relay_backend(monkeypatch):
    """AC — the specialized privacy="local" tier is selected by
    build_backend_for_tier as a RelayBackend (NOT a CloudBackend, and NOT any new
    backend class). Construction is network-free; `model` is set so no discovery
    probe runs."""
    from anvil_serving.router.backends.cloud import CloudBackend
    from anvil_serving.router.backends.relay import RelayBackend
    from anvil_serving.router.serve import build_backend_for_tier

    monkeypatch.delenv("ANVIL_SPECIALIST_KEY", raising=False)  # auth optional for a local relay
    tier = load(str(EXAMPLE_FLEXIBILITY)).tier("specialist-vllm")
    backend = build_backend_for_tier(tier)
    assert isinstance(backend, RelayBackend)
    # RelayBackend subclasses CloudBackend; assert it's the relay, not a raw cloud tier.
    assert type(backend) is RelayBackend
    assert type(backend) is not CloudBackend


# ── router-service:T001 — top-level [server] table (front-door auth) ────────
#
# `load_server_config` reads the OPTIONAL top-level `[server]` table
# independently of `[router]` (ADR-0004). It never reads os.environ -- it only
# validates the `auth_env` NAME shape, exactly like a tier's `auth_env`.
def _write_raw_toml(tmp_path: pathlib.Path, text: str) -> str:
    p = tmp_path / "cfg.toml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_server_table_absent_yields_auth_off(tmp_path):
    """No [server] table at all -> ServerConfig(auth_env=None), auth OFF."""
    path = _write_raw_toml(tmp_path, "[router]\n" + _BASE_TIER)
    cfg = load_server_config(path)
    assert cfg == ServerConfig(auth_env=None)
    assert cfg.auth_env is None


def test_server_table_present_without_auth_env_yields_auth_off(tmp_path):
    path = _write_raw_toml(
        tmp_path,
        "[server]\n\n[router]\n" + _BASE_TIER,
    )
    cfg = load_server_config(path)
    assert cfg.auth_env is None


def test_server_auth_env_valid_name_parses(tmp_path):
    path = _write_raw_toml(
        tmp_path,
        '[server]\nauth_env = "ANVIL_ROUTER_TOKEN"\n\n[router]\n' + _BASE_TIER,
    )
    cfg = load_server_config(path)
    assert cfg.auth_env == "ANVIL_ROUTER_TOKEN"
    # Loading the [router] block independently is unaffected by [server].
    assert isinstance(load(path), RouterConfig)


def test_server_table_not_a_table_raises(tmp_path):
    path = _write_raw_toml(tmp_path, 'server = "oops"\n\n[router]\n' + _BASE_TIER)
    with pytest.raises(ConfigError):
        load_server_config(path)


@pytest.mark.parametrize(
    "bad_name",
    [
        "anvil_router_token",  # lowercase
        "1ANVIL_TOKEN",  # leading digit
        "ANVIL-ROUTER-TOKEN",  # hyphens not allowed
        "ANVIL TOKEN",  # whitespace
        "",  # empty
    ],
)
def test_server_auth_env_bad_name_raises(tmp_path, bad_name):
    path = _write_raw_toml(
        tmp_path,
        f'[server]\nauth_env = "{bad_name}"\n\n[router]\n' + _BASE_TIER,
    )
    with pytest.raises(ConfigError):
        load_server_config(path)


def test_server_auth_env_secret_shaped_literal_raises(tmp_path):
    """A pasted AWS-access-key-id-shaped literal fits the env-name charset but
    must still be rejected as a secret literal, not a name (defense in depth,
    mirrors the tier auth_env guard)."""
    path = _write_raw_toml(
        tmp_path,
        '[server]\nauth_env = "AKIAABCDEFGHIJKLMNOP"\n\n[router]\n' + _BASE_TIER,
    )
    with pytest.raises(ConfigError):
        load_server_config(path)


def test_server_auth_env_non_string_raises(tmp_path):
    path = _write_raw_toml(
        tmp_path, "[server]\nauth_env = 12345\n\n[router]\n" + _BASE_TIER,
    )
    with pytest.raises(ConfigError):
        load_server_config(path)


# ── flexibility:T011 — fakoli-dark two-mode split (ADR-0011) ─────────────────
#
# The deploy is split into two mode configs that never overlap: the agentic
# config is the captured-live SGLang deploy, byte-for-byte; the flexibility
# config is the any-engine, specialized-tier (ADR-0010) counterpart. Only one
# mode is live at a time (a config reload/restart, never per-request).
def test_fakoli_agentic_is_byte_identical_to_live():
    """AC — anvil-router.agentic.toml must be BYTE-IDENTICAL to the captured-live
    config: the agentic mode config is the live deploy, isolated + unchanged."""
    assert FAKOLI_LIVE.exists(), "anvil-router.live.toml missing"
    assert FAKOLI_AGENTIC.exists(), "anvil-router.agentic.toml missing"
    assert (
        FAKOLI_AGENTIC.read_bytes() == FAKOLI_LIVE.read_bytes()
    ), "agentic config drifted from the captured-live config (must be byte-identical)"


def test_fakoli_agentic_config_loads():
    """The agentic mode config parses (it is the live SGLang two-tier deploy)."""
    cfg = load(str(FAKOLI_AGENTIC))
    assert isinstance(cfg, RouterConfig)
    assert {t.id for t in cfg.tiers} == {"fast-local", "heavy-local"}
    # It carries none of the flexibility:T007 engine fields (unchanged live config).
    for t in cfg.tiers:
        assert t.engine is None and t.quantization is None


def test_fakoli_flexibility_config_loads():
    """The flexibility mode config parses and is a distinct, separate config."""
    cfg = load(str(FAKOLI_FLEXIBILITY))
    assert isinstance(cfg, RouterConfig)
    assert cfg.tiers, "flexibility config declares no tiers"
    # Distinct tier set from the agentic config (no shared tier ids).
    agentic_ids = {t.id for t in load(str(FAKOLI_AGENTIC)).tiers}
    flex_ids = {t.id for t in cfg.tiers}
    assert flex_ids.isdisjoint(agentic_ids), (
        f"flexibility and agentic configs share tier ids: {flex_ids & agentic_ids}"
    )


def test_fakoli_flexibility_has_specialized_engine_tier():
    """AC — the flexibility config declares at least one specialized-engine tier
    using the flexibility:T007 `engine`/`timeout` fields (ADR-0010)."""
    cfg = load(str(FAKOLI_FLEXIBILITY))
    specialized = [t for t in cfg.tiers if t.engine is not None]
    assert specialized, "flexibility config has no tier with an `engine` set"
    for t in specialized:
        # A specialized-engine tier documents its engine and overrides the global
        # relay_timeout for its (potentially slow) large-prefill path.
        assert isinstance(t.engine, str) and t.engine
        assert t.timeout is not None and t.timeout > 0


def test_fakoli_flexibility_endpoints_never_localhost():
    """Project gotcha #1: 127.0.0.1 / host.docker.internal only, never localhost."""
    cfg = load(str(FAKOLI_FLEXIBILITY))
    for t in cfg.tiers:
        assert "localhost" not in t.base_url, f"tier {t.id!r} base_url uses localhost"
        assert (
            "127.0.0.1" in t.base_url or "host.docker.internal" in t.base_url
        ), f"tier {t.id!r} base_url is neither 127.0.0.1 nor host.docker.internal"


def test_fakoli_flexibility_presets_reference_known_tiers():
    """Every flexibility preset resolves to a declared tier (config is coherent)."""
    cfg = load(str(FAKOLI_FLEXIBILITY))
    known = {t.id for t in cfg.tiers}
    for name, cands in cfg.presets.items():
        for cid in cands:
            assert cid in known, f"flexibility preset {name} -> unknown tier {cid}"
