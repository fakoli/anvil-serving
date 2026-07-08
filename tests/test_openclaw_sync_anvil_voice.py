import json
from pathlib import Path

from anvil_serving import harness
from anvil_serving.voice import config as voice_config


class _Tier:
    def __init__(self, context_limit):
        self.context_limit = context_limit


class _Config:
    def __init__(self, presets, tiers):
        self.presets = presets
        self._tiers = tiers

    def tier(self, tier_id):
        return self._tiers[tier_id]


def _cfg():
    return _Config(
        presets={"chat": ("fast", "heavy"), "chat-fast": ("fast", "heavy")},
        tiers={"fast": _Tier(32768), "heavy": _Tier(131072)},
    )


def test_openclaw_voice_sync_emits_anvil_talk_realtime_config(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://127.0.0.1:8765/v1/realtime",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    talk = payload["talk"]
    realtime = payload["talk"]["realtime"]
    anvil = realtime["providers"]["anvil"]

    assert talk["consultModel"] == "anvil/chat-fast"
    assert talk["consultThinkingLevel"] == "off"
    assert talk["consultBootstrapContextMode"] == "lightweight"
    assert realtime["mode"] == "realtime"
    assert realtime["transport"] == "gateway-relay"
    assert realtime["brain"] == "agent-consult"
    assert realtime["consultRouting"] == "force-agent-consult"
    assert realtime["provider"] == "anvil"
    assert anvil["realtimeUrl"] == "ws://127.0.0.1:8765/v1/realtime"
    assert anvil["model"] == "fast-local"
    assert "apiKey" not in anvil


def test_openclaw_voice_sync_can_override_consult_model(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_consult_model="anvil/chat",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["talk"]["consultModel"] == "anvil/chat"


def test_openclaw_voice_sync_can_override_consult_thinking_level(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_consult_thinking_level="low",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["talk"]["consultThinkingLevel"] == "low"


def test_openclaw_voice_sync_can_override_consult_bootstrap_context_mode(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_consult_bootstrap_context_mode="full",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["talk"]["consultBootstrapContextMode"] == "full"


def test_openclaw_voice_sync_rejects_bad_consult_thinking_level(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_consult_thinking_level="turbo",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "voice consult thinking level" in capsys.readouterr().err


def test_openclaw_voice_sync_rejects_bad_consult_bootstrap_context_mode(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_consult_bootstrap_context_mode="compact",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "voice consult bootstrap context mode" in capsys.readouterr().err


def test_openclaw_voice_sync_replaces_existing_consult_thinking_level(tmp_path):
    existing = tmp_path / "openclaw.json"
    existing.write_text(
        json.dumps(
            {
                "talk": {
                    "consultThinkingLevel": "low",
                    "consultBootstrapContextMode": "full",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    rc = harness.cmd_sync_openclaw(
        "router.toml",
        out=str(existing),
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    payload = json.loads(existing.read_text(encoding="utf-8"))
    assert payload["talk"]["consultThinkingLevel"] == "off"
    assert payload["talk"]["consultBootstrapContextMode"] == "lightweight"


def test_openclaw_voice_sync_falls_back_to_chat_consult_model(capsys):
    cfg = _Config(
        presets={"chat": ("fast", "heavy")},
        tiers={"fast": _Tier(32768), "heavy": _Tier(131072)},
    )
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        _load=lambda _path: cfg,
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["talk"]["consultModel"] == "anvil/chat"


def test_openclaw_voice_sync_can_emit_env_secretref(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
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
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://100.87.34.66:8765/v1/realtime",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "--voice-api-key-env" in capsys.readouterr().err


def test_openclaw_voice_sync_accepts_private_realtime_url_with_env_secretref(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://100.87.34.66:8765/v1/realtime",
        voice_api_key_env="ANVIL_VOICE_REALTIME_TOKEN",
        _load=lambda _path: _cfg(),
    )

    assert rc == 0
    anvil = json.loads(capsys.readouterr().out)["talk"]["realtime"]["providers"]["anvil"]
    assert anvil["realtimeUrl"] == "ws://100.87.34.66:8765/v1/realtime"
    assert anvil["apiKey"]["id"] == "ANVIL_VOICE_REALTIME_TOKEN"


def test_openclaw_voice_sync_rejects_public_realtime_url(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="wss://8.8.8.8:8765/v1/realtime",
        voice_api_key_env="ANVIL_VOICE_REALTIME_TOKEN",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "loopback, private, or tailnet" in capsys.readouterr().err


def test_openclaw_voice_sync_rejects_loopback_alias(capsys):
    rc = harness.cmd_sync_openclaw(
        "router.toml",
        base_url="http://100.87.34.66:8000/v1",
        api_key_env="ANVIL_ROUTER_TOKEN",
        voice=True,
        voice_realtime_url="ws://" + "local" + "host" + ":8765/v1/realtime",
        _load=lambda _path: _cfg(),
    )

    assert rc == 2
    assert "127.0.0.1" in capsys.readouterr().err


def test_openclaw_anvil_voice_example_manifest_is_valid_and_hygienic():
    path = Path("examples/voice/openclaw-anvil-voice.toml")
    text = path.read_text(encoding="utf-8")

    assert "local" + "host" not in text.lower()
    for marker in ("sk" + "-", "hf" + "_", "hf" + "-", "ghp" + "_", "ghp" + "-"):
        assert marker not in text

    data = voice_config.load_manifest(str(path))
    assert data["voice"]["name"] == "anvil-voice-openclaw"
    assert data["voice"]["realtime_host"] == "127.0.0.1"
    assert data["voice"]["realtime_port"] == 8765
    assert data["voice"]["llm"]["base_url"] == "http://100.87.34.66:8000/v1"
    assert data["voice"]["llm"]["model"] == "fast-local"
    assert data["voice"]["llm"]["api_key_env"] == "ANVIL_ROUTER_TOKEN"
    assert data["voice"]["stt"]["base_url"] == "http://100.87.34.66:30110/v1"
    assert data["voice"]["stt"]["model"] == "tdt_ctc-110m"
    assert data["voice"]["stt"]["lifecycle"] == "external"
    assert data["voice"]["tts"]["base_url"] == "http://100.87.34.66:30111/v1"
    assert data["voice"]["tts"]["model"] == "kokoro"
    assert data["voice"]["tts"]["lifecycle"] == "external"
    assert data["voice"]["tts"]["response_format"] == "pcm"

    mini = voice_config.load_manifest(str(path), profile="mini-audio")
    assert mini["voice"]["stt"]["base_url"] == "http://127.0.0.1:30010/v1"
    assert mini["voice"]["stt"]["lifecycle"] == "native"
    assert mini["voice"]["stt"]["start_command"]
    assert mini["voice"]["tts"]["base_url"] == "http://127.0.0.1:30011/v1"
    assert mini["voice"]["tts"]["lifecycle"] == "native"
    assert mini["voice"]["tts"]["start_command"]
