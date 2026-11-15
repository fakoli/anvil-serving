import json

from anvil_serving import harness


class _Tier:
    def __init__(self, context_limit, modalities=None):
        self.context_limit = context_limit
        self.params = {"capabilities": {"modalities": modalities or ["text"]}}


class _Config:
    def __init__(self, model_routes, tiers):
        self.model_routes = model_routes
        self._tiers = tiers

    def tier(self, tier_id):
        return self._tiers[tier_id]


def _cfg():
    return _Config(
        model_routes={"llm.primary": "heavy", "llm.voice": "fast"},
        tiers={"fast": _Tier(32768), "heavy": _Tier(131072)},
    )


def test_openclaw_sync_refreshes_anvil_models_without_erasing_other_providers(tmp_path):
    existing = tmp_path / "openclaw.json"
    existing.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"models": {
                    "anthropic/claude-sonnet": {},
                    "anvil/llm.stale": {},
                }}},
                "models": {"providers": {
                    "anvil": {"apiKey": {
                        "source": "file", "provider": "default", "id": "/anvil/routerToken",
                    }},
                    "other": {"models": []},
                }},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = _Config(
        model_routes={"llm.primary": "heavy", "llm.thinking": "fast"},
        tiers={
            "fast": _Tier(262144, ["text", "image"]),
            "heavy": _Tier(262144, ["text", "image"]),
        },
    )

    rc = harness.cmd_sync_openclaw(
        "router.toml",
        out=str(existing),
        base_url="http://100.64.0.10:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        _load=lambda _path: cfg,
    )

    assert rc == 0
    payload = json.loads(existing.read_text(encoding="utf-8"))
    assert [
        model["id"] for model in payload["models"]["providers"]["anvil"]["models"]
    ] == ["llm.primary", "llm.thinking"]
    assert [
        model["input"] for model in payload["models"]["providers"]["anvil"]["models"]
    ] == [["text", "image"], ["text", "image"]]
    assert payload["agents"]["defaults"]["models"] == {
        "anthropic/claude-sonnet": {},
        "anvil/llm.primary": {},
        "anvil/llm.thinking": {},
    }
    assert payload["models"]["providers"]["anvil"]["apiKey"] == {
        "source": "file", "provider": "default", "id": "/anvil/routerToken",
    }
    assert "other" in payload["models"]["providers"]


def test_openclaw_voice_sync_can_emit_env_secretref(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.64.0.10:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_api_key_env="ANVIL_VOICE_REALTIME_TOKEN",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    anvil = payload["talk"]["realtime"]["providers"]["anvil"]
    assert anvil["apiKey"] == {
        "source": "env",
        "provider": "default",
        "id": "ANVIL_VOICE_REALTIME_TOKEN",
    }


def test_openclaw_voice_sync_requires_env_secretref_for_private_realtime_url(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.64.0.10:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://100.64.0.10:8765/v1/realtime",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "--voice-api-key-env" in capsys.readouterr().err


def test_openclaw_voice_sync_accepts_private_realtime_url_with_env_secretref(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.64.0.10:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://100.64.0.10:8765/v1/realtime",
        voice_api_key_env="ANVIL_VOICE_REALTIME_TOKEN",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    anvil = json.loads(capsys.readouterr().out)["talk"]["realtime"]["providers"]["anvil"]
    assert anvil["realtimeUrl"] == "ws://100.64.0.10:8765/v1/realtime"
    assert anvil["apiKey"]["id"] == "ANVIL_VOICE_REALTIME_TOKEN"
