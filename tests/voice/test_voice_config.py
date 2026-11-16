"""Tests for anvil_serving.voice.config -- manifest loading + hygiene validation.

Dependency-light: stdlib only, no network, no GPU, no torch. Exercises the
shipped example manifest plus synthetic manifests built in-memory / via
tmp_path so nothing here depends on real STT/TTS/router serves being up.
"""

from dataclasses import replace

import pytest

from anvil_serving.topology import load_topology
from anvil_serving.voice import config as voice_config


def _valid_manifest():
    return {
        "voice": {
            "name": "anvil-voice",
            "realtime_host": "127.0.0.1",
            "realtime_port": 8765,
            "llm": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "chat",
                "stream": True,
                "api_key_env": "ANVIL_ROUTER_TOKEN",
            },
            "stt": {
                "base_url": "http://127.0.0.1:8090/v1",
                "model": "parakeet-tdt-0.6b-v3",
            },
            "tts": {
                "base_url": "http://127.0.0.1:8091/v1",
                "model": "kokoro-82m",
            },
        }
    }


def _reference_topology_with_resource(kind, **changes):
    topology = load_topology("examples/primary-node/operator-topology.toml")
    resources = tuple(
        replace(resource, **changes) if resource.role == f"{kind}-serve" else resource
        for resource in topology.resources
    )
    return replace(topology, resources=resources)


def _record_transport_selection(monkeypatch):
    from anvil_serving import targets

    selected = []
    monkeypatch.setattr(targets, "_select_transport", lambda *args: selected.append(args))
    return selected


def test_openclaw_voice_manifest_profiles_are_valid():
    raw = voice_config.load_manifest("examples/voice/openclaw-anvil-voice.toml")
    assert raw["voice"]["stt"]["base_url"] == "http://100.64.0.10:30110/v1"
    assert raw["voice"]["tts"]["base_url"] == "http://100.64.0.10:30111/v1"
    assert raw["voice"]["stt"]["lifecycle"] == "external"
    assert raw["voice"]["tts"]["lifecycle"] == "external"
    assert voice_config.profile_names(raw) == [
        "candidate-gemma4-12b",
        "candidate-gemma4-e4b",
        "candidate-qwen3-32b",
        "dark-audio",
        "gepard-fast-tts",
        "mini-audio",
        "mini-dark-audio-proxy",
        "mini-validation",
    ]

    mini = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="mini-audio",
    )
    assert mini["voice"]["llm"]["speech_chunk_max_chars"] == 56
    assert mini["voice"]["stt"]["base_url"] == "http://127.0.0.1:30010/v1"
    assert mini["voice"]["tts"]["base_url"] == "http://127.0.0.1:30011/v1"

    mini = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="mini-audio",
    )
    assert mini["voice"]["llm"]["speech_chunk_max_chars"] == 56
    assert mini["voice"]["stt"]["base_url"] == "http://127.0.0.1:30010/v1"
    assert mini["voice"]["tts"]["base_url"] == "http://127.0.0.1:30011/v1"

    dark = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="dark-audio",
    )
    assert dark["voice"]["llm"]["speech_chunk_max_chars"] == 72
    assert dark["voice"]["stt"]["base_url"] == "http://100.64.0.10:30110/v1"
    assert dark["voice"]["tts"]["base_url"] == "http://100.64.0.10:30111/v1"
    assert dark["voice"]["stt"]["lifecycle"] == "external"
    assert "start_command" not in dark["voice"]["stt"]
    assert "pid_file" not in dark["voice"]["tts"]

    gepard = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="gepard-fast-tts",
    )
    assert gepard["voice"]["stt"]["base_url"] == "http://100.64.0.10:30110/v1"
    assert gepard["voice"]["tts"]["base_url"] == "http://100.64.0.10:39111"
    assert gepard["voice"]["tts"]["model"] == "gepard-1.0"
    assert gepard["voice"]["tts"]["protocol"] == "gepard"
    assert gepard["voice"]["tts"]["source_sample_rate"] == 22050
    assert gepard["voice"]["tts"]["target_sample_rate"] == 16000
    assert gepard["voice"]["tts"]["lifecycle"] == "external"

    proxy = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="mini-dark-audio-proxy",
    )
    assert proxy["voice"]["stt"]["base_url"] == "http://127.0.0.1:30110/v1"
    assert proxy["voice"]["tts"]["base_url"] == "http://127.0.0.1:30111/v1"
    assert proxy["voice"]["stt"]["lifecycle"] == "external"
    assert "start_command" not in proxy["voice"]["stt"]
    assert "pid_file" not in proxy["voice"]["tts"]

    validation = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="mini-validation",
    )
    assert validation["voice"]["llm"]["system_prompt"].endswith("I understand.")
    assert validation["voice"]["llm"]["temperature"] == 0.0
    assert validation["voice"]["llm"]["max_tokens"] == 8

    candidate = voice_config.load_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="candidate-qwen3-32b",
    )
    assert candidate["voice"]["llm"]["base_url"] == "http://100.64.0.10:39000/v1"
    assert candidate["voice"]["llm"]["model"] == "qwen3-32b-nvfp4"
    assert candidate["voice"]["llm"]["api_key_env"] == "ANVIL_CANDIDATE_LLM_TOKEN"
    assert candidate["voice"]["stt"]["base_url"] == "http://100.64.0.10:30110/v1"
    assert candidate["voice"]["tts"]["base_url"] == "http://100.64.0.10:30111/v1"
    assert candidate["voice"]["stt"]["lifecycle"] == "external"
    assert candidate["voice"]["tts"]["lifecycle"] == "external"

    composed = voice_config.resolve_manifest(
        "examples/voice/openclaw-anvil-voice.toml",
        profile="dark-audio",
        candidate_overlay=voice_config.load_raw_manifest(
            "examples/voice/candidates/qwen3-32b-nvfp4.toml"
        ),
        candidate="qwen3-32b-nvfp4",
    )
    assert composed.profile == "dark-audio"
    assert composed.candidate == "qwen3-32b-nvfp4"
    assert composed.llm_base_url == "http://100.64.0.10:39000/v1"
    assert composed.stt_base_url == "http://100.64.0.10:30110/v1"
    assert composed.tts_base_url == "http://100.64.0.10:30111/v1"


def test_describe_has_no_secret_and_summarizes():
    data = _valid_manifest()
    summary = voice_config.describe(data)
    assert "anvil-voice" in summary
    assert "127.0.0.1:8765" in summary
    assert "ANVIL_ROUTER_TOKEN" not in summary  # env var name isn't a secret, but shouldn't leak either


@pytest.mark.parametrize("bad_host", ["localhost", "LOCALHOST"])
def test_rejects_localhost_in_llm_base_url(bad_host):
    data = _valid_manifest()
    data["voice"]["llm"]["base_url"] = "http://%s:8000/v1" % bad_host
    with pytest.raises(voice_config.ConfigError, match="localhost"):
        voice_config.validate_manifest(data)


def test_rejects_url_embedded_credentials():
    data = _valid_manifest()
    data["voice"]["llm"]["base_url"] = "http://user:pass@127.0.0.1:8000/v1"
    with pytest.raises(voice_config.ConfigError, match="credentials"):
        voice_config.validate_manifest(data)


@pytest.mark.parametrize("secret_key", ["api_key", "token", "secret", "password"])
def test_rejects_secret_literal_key_names(secret_key):
    data = _valid_manifest()
    data["voice"]["llm"][secret_key] = "whatever-value"
    with pytest.raises(voice_config.ConfigError, match="env var name"):
        voice_config.validate_manifest(data)


@pytest.mark.parametrize("literal", ["sk-abcdefg12345", "hf_abcdefg12345", "ghp_abcdefg12345"])
def test_rejects_secret_shaped_values_even_under_env_key(literal):
    data = _valid_manifest()
    # Someone mistakenly pastes a live secret into the *_env field.
    data["voice"]["llm"]["api_key_env"] = literal
    with pytest.raises(voice_config.ConfigError):
        voice_config.validate_manifest(data)


def test_native_audio_lifecycle_rejects_secret_literal_in_command():
    data = _valid_manifest()
    data["voice"]["stt"].update({
        "lifecycle": "native",
        "start_command": "python -m mlx_audio.server --token sk-proj-secret12345",
    })
    with pytest.raises(voice_config.ConfigError, match="secret literal"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_timeout():
    data = _valid_manifest()
    data["voice"]["llm"]["timeout"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_tool_result_timeout():
    data = _valid_manifest()
    data["voice"]["llm"]["tool_result_timeout"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


# --------------------------------------------------------------------------- #
# [voice.proxy] -- optional managed-container / direct-endpoint proxy config
# --------------------------------------------------------------------------- #


def test_rejects_proxy_ready_url_with_credentials():
    data = _valid_manifest()
    data["voice"]["proxy"] = {"ready_url": "http://user:pass@127.0.0.1:8765/usage"}
    with pytest.raises(voice_config.ConfigError, match="credentials"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_proxy_ready_timeout():
    data = _valid_manifest()
    data["voice"]["proxy"] = {"ready_timeout": 0}
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_resolve_secret_reads_named_env_var(monkeypatch):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "totally-a-token-value")
    llm = _valid_manifest()["voice"]["llm"]
    assert voice_config.resolve_secret(llm, "api_key") == "totally-a-token-value"


def test_resolve_secret_returns_none_when_absent_and_not_required():
    table = {}
    assert voice_config.resolve_secret(table, "api_key", required=False) is None


def test_resolve_secret_raises_when_required_and_absent():
    table = {}
    with pytest.raises(voice_config.ConfigError, match="required"):
        voice_config.resolve_secret(table, "api_key", required=True)


def test_resolve_secret_raises_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("ANVIL_VOICE_MISSING_TOKEN", raising=False)
    table = {"api_key_env": "ANVIL_VOICE_MISSING_TOKEN"}
    with pytest.raises(voice_config.ConfigError, match="not set"):
        voice_config.resolve_secret(table, "api_key")
