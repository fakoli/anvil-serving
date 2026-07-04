"""Tests for `anvil-serving harness` — render the OpenClaw harness config from the router config.

The RouterConfig loader is injected (`_load`), so these run with no config file and no network.
"""
import json

from anvil_serving import harness


class _Tier:
    def __init__(self, context_limit):
        self.context_limit = context_limit


class _Config:
    """Minimal stand-in for RouterConfig: `.presets` + `.tier(id).context_limit`."""
    def __init__(self, presets, tiers):
        self.presets = presets
        self._tiers = tiers

    def tier(self, tid):
        return self._tiers[tid]


def _cfg():
    return _Config(
        presets={"planning": ("heavy",), "chat": ("heavy", "fast"),
                 "quick-edit": ("heavy", "fast"), "review": ("heavy",)},
        tiers={"heavy": _Tier(131072), "fast": _Tier(32768)},
    )


# ---- rendering ---------------------------------------------------------------

def test_render_one_model_per_preset_with_max_routed_context():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x:8000/v1")
    models = {m["id"]: m for m in prov["models"]["providers"]["anvil"]["models"]}
    assert set(models) == {"planning", "chat", "quick-edit", "review"}
    # contextWindow = the LARGEST tier the preset can route to (clamp gotcha)
    assert models["planning"]["contextWindow"] == 131072       # heavy only
    assert models["chat"]["contextWindow"] == 131072           # max(heavy, fast) -> heavy
    assert models["quick-edit"]["contextWindow"] == 131072
    # display name title-cases the preset id
    assert models["quick-edit"]["name"] == "Anvil · Quick Edit"
    # review advertises image input
    assert models["review"]["input"] == ["text", "image"]
    assert models["chat"]["input"] == ["text"]


def test_no_stale_thinking_overrides():
    # the router owns reasoning/thinking per tier now, so the harness must NOT re-declare them.
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://x/v1")
    assert prov["agents"]["defaults"]["models"] == {}


def test_provider_shape_and_token_by_reference():
    prov = harness.render_openclaw_provider(_cfg(), base_url="http://h:8000/v1", api_key_env="TOK")
    anvil = prov["models"]["providers"]["anvil"]
    assert anvil["baseUrl"] == "http://h:8000/v1"
    assert anvil["apiKey"] == "${TOK}"          # by name, never the secret
    assert anvil["api"] == "openai-completions"
    assert prov["models"]["mode"] == "merge"
    assert prov["plugins"]["entries"]["anvil-intent-router"]["hooks"]["allowConversationAccess"] is True


# ---- cmd_sync_openclaw -------------------------------------------------------

def test_sync_emits_valid_json_to_stdout(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda p: _cfg())
    assert rc == 0
    d = json.loads(capsys.readouterr().out)          # valid JSON
    assert len(d["models"]["providers"]["anvil"]["models"]) == 4


def test_sync_writes_out_file(tmp_path, capsys):
    p = tmp_path / "openclaw.json"
    rc = harness.cmd_sync_openclaw("r.toml", out=str(p), base_url="http://h/v1",
                                   api_key_env="ANVIL_ROUTER_TOKEN", _load=lambda _p: _cfg())
    assert rc == 0
    assert len(json.loads(p.read_text(encoding="utf-8"))["models"]["providers"]["anvil"]["models"]) == 4
    assert "OpenClaw provider config" in capsys.readouterr().out


def test_sync_skills_not_implemented_yet(capsys):
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   skills=True, _load=lambda p: _cfg())
    assert rc == 2
    assert "not implemented" in capsys.readouterr().err


def test_sync_missing_config_errors():
    def boom(p):
        raise FileNotFoundError()
    rc = harness.cmd_sync_openclaw("nope.toml", base_url="http://h/v1", api_key_env="T", _load=boom)
    assert rc == 2


def test_sync_no_presets_errors(capsys):
    empty = _Config(presets={}, tiers={})
    rc = harness.cmd_sync_openclaw("r.toml", base_url="http://h/v1", api_key_env="T",
                                   _load=lambda p: empty)
    assert rc == 1
    assert "no [router.presets]" in capsys.readouterr().err


# ---- CLI dispatch ------------------------------------------------------------

def test_main_dispatches_sync_openclaw(monkeypatch):
    seen = {}
    def fake(cfg, **k):
        seen["cfg"], seen["k"] = cfg, k
        return 0
    monkeypatch.setattr(harness, "cmd_sync_openclaw", fake)
    rc = harness.main(["sync", "openclaw", "--config", "r.toml", "--base-url", "http://h:8000/v1"])
    assert rc == 0
    assert seen["cfg"] == "r.toml"
    assert seen["k"]["base_url"] == "http://h:8000/v1"
    assert seen["k"]["api_key_env"] == "ANVIL_ROUTER_TOKEN"
