"""Tests for anvil_serving.voice.config -- manifest loading + hygiene validation.

Dependency-light: stdlib only, no network, no GPU, no torch. Exercises the
shipped example manifest plus synthetic manifests built in-memory / via
tmp_path so nothing here depends on real STT/TTS/router serves being up.
"""

import copy

import pytest

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


def test_shipped_example_manifest_is_valid():
    data = voice_config.load_manifest(voice_config.DEFAULT_CONFIG)
    assert data["voice"]["name"] == "anvil-voice"
    assert data["voice"]["llm"]["base_url"] == "http://127.0.0.1:8000/v1"


def test_fakoli_mini_manifest_is_valid_and_preserves_route_contract():
    data = voice_config.load_manifest("examples/voice/fakoli-mini.toml")

    assert data["voice"]["name"] == "anvil-voice-fakoli-mini"
    assert data["voice"]["llm"]["base_url"] == "http://100.87.34.66:8000/v1"
    assert data["voice"]["llm"]["api_key_env"] == "ANVIL_ROUTER_TOKEN"
    assert data["voice"]["llm"]["expected_endpoint_host"] == "100.87.34.66"
    assert data["voice"]["llm"]["expected_route_provider"] == "fast-local"
    assert data["voice"]["llm"]["expected_route_model"] == "qwen36-27b"
    assert data["voice"]["llm"]["expected_route_tier"] == "local"
    assert data["voice"]["llm"]["system_prompt"].endswith("I understand.")
    assert data["voice"]["llm"]["temperature"] == 0.0
    assert data["voice"]["llm"]["max_tokens"] == 8
    assert data["voice"]["stt"]["lifecycle"] == "native"
    assert data["voice"]["stt"]["stream"] is False
    assert data["voice"]["stt"]["response_format"] == "json"
    assert data["voice"]["stt"]["timeout"] == 120.0
    assert data["voice"]["stt"]["start_command"].startswith(".venv/bin/python -m mlx_audio.server")
    assert data["voice"]["stt"]["pid_file"] == "/tmp/anvil-voice-mini/stt.pid"
    assert data["voice"]["tts"]["lifecycle"] == "native"
    assert data["voice"]["tts"]["response_format"] == "pcm"
    assert data["voice"]["tts"]["timeout"] == 120.0
    assert data["voice"]["tts"]["start_command"].startswith(".venv/bin/python -m mlx_audio.server")


def test_fakoli_dark_manifest_marks_sidecars_external():
    data = voice_config.load_manifest("examples/voice/fakoli-dark.toml")

    assert data["voice"]["stt"]["lifecycle"] == "external"
    assert data["voice"]["tts"]["lifecycle"] == "external"


def test_openclaw_voice_manifest_profiles_are_valid():
    raw = voice_config.load_manifest("examples/voice/openclaw-anvil-voice.toml")
    assert voice_config.profile_names(raw) == [
        "candidate-gemma4-12b",
        "candidate-gemma4-e4b",
        "candidate-qwen3-32b",
        "dark-audio",
        "mini-audio",
        "mini-validation",
    ]

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
    assert dark["voice"]["stt"]["base_url"] == "http://100.87.34.66:30110/v1"
    assert dark["voice"]["tts"]["base_url"] == "http://100.87.34.66:30111/v1"
    assert dark["voice"]["stt"]["lifecycle"] == "external"
    assert "start_command" not in dark["voice"]["stt"]
    assert "pid_file" not in dark["voice"]["tts"]

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
    assert candidate["voice"]["llm"]["base_url"] == "http://100.87.34.66:39000/v1"
    assert candidate["voice"]["llm"]["model"] == "qwen3-32b-nvfp4"
    assert candidate["voice"]["llm"]["api_key_env"] == "ANVIL_CANDIDATE_LLM_TOKEN"
    assert candidate["voice"]["llm"]["expected_route_provider"] == "direct-vllm"
    assert candidate["voice"]["llm"]["expected_route_model"] == "qwen3-32b-nvfp4"
    assert candidate["voice"]["stt"]["base_url"] == "http://127.0.0.1:30010/v1"
    assert candidate["voice"]["tts"]["base_url"] == "http://127.0.0.1:30011/v1"


def test_resolve_manifest_data_returns_profile_identity():
    data = _valid_manifest()
    data["voice"]["profiles"] = {
        "dark-audio": {
            "llm": {
                "base_url": "http://100.87.34.66:8000/v1",
                "model": "fast-local",
            },
            "stt": {
                "base_url": "http://100.87.34.66:30110/v1",
                "model": "tdt_ctc-110m",
            },
            "tts": {
                "base_url": "http://100.87.34.66:30111/v1",
                "model": "kokoro",
            },
        },
    }

    resolved = voice_config.resolve_manifest_data(data, profile="dark-audio")

    assert resolved.profile == "dark-audio"
    assert resolved.candidate is None
    assert resolved.llm_base_url == "http://100.87.34.66:8000/v1"
    assert resolved.llm_model == "fast-local"
    assert resolved.stt_model == "tdt_ctc-110m"
    assert resolved.tts_model == "kokoro"
    assert resolved.identity()["tts_base_url"] == "http://100.87.34.66:30111/v1"


def test_candidate_overlay_merges_after_profile_without_mutating_source():
    data = _valid_manifest()
    data["voice"]["profiles"] = {
        "mini-audio": {
            "llm": {"model": "fast-local"},
            "stt": {"model": "mini-stt"},
        },
    }
    original = copy.deepcopy(data)

    resolved = voice_config.resolve_manifest_data(
        data,
        profile="mini-audio",
        candidate="gemma4-12b",
        candidate_overlay={
            "llm": {
                "base_url": "http://127.0.0.1:39000/v1",
                "model": "gemma4-12b",
            },
            "tts": {"model": "candidate-tts"},
        },
    )

    assert data == original
    assert resolved.profile == "mini-audio"
    assert resolved.candidate == "gemma4-12b"
    assert resolved.llm_base_url == "http://127.0.0.1:39000/v1"
    assert resolved.llm_model == "gemma4-12b"
    assert resolved.stt_model == "mini-stt"
    assert resolved.tts_model == "candidate-tts"
    assert "profiles" not in resolved.data["voice"]


def test_candidate_overlay_can_be_wrapped_in_voice_table():
    resolved = voice_config.resolve_manifest_data(
        _valid_manifest(),
        candidate="qwen3-32b",
        candidate_overlay={
            "voice": {
                "llm": {
                    "base_url": "http://127.0.0.1:39001/v1",
                    "model": "qwen3-32b",
                },
            },
        },
    )

    assert resolved.llm_model == "qwen3-32b"
    assert resolved.llm_base_url == "http://127.0.0.1:39001/v1"


def test_candidate_overlay_missing_endpoint_is_clear():
    with pytest.raises(voice_config.ConfigError, match=r"voice\.llm\.base_url"):
        voice_config.resolve_manifest_data(
            _valid_manifest(),
            candidate="broken",
            candidate_overlay={"llm": {"base_url": ""}},
        )


def test_unknown_voice_profile_is_rejected(tmp_path):
    manifest = tmp_path / "voice.toml"
    manifest.write_text(
        """
[voice]
name = "test-voice"
realtime_host = "127.0.0.1"
realtime_port = 8765

[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "chat"

[voice.stt]
base_url = "http://127.0.0.1:30010/v1"
model = "stt"

[voice.tts]
base_url = "http://127.0.0.1:30011/v1"
model = "tts"

[voice.profiles.mini-audio]
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(voice_config.ConfigError, match="unknown voice profile"):
        voice_config.load_manifest(str(manifest), profile="missing")


def test_shipped_example_default_path_used_when_none_given():
    # load_manifest(None) should fall back to DEFAULT_CONFIG and succeed.
    data = voice_config.load_manifest(None)
    assert "voice" in data


def test_valid_manifest_passes():
    voice_config.validate_manifest(_valid_manifest())  # should not raise


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


def test_rejects_localhost_realtime_host():
    data = _valid_manifest()
    data["voice"]["realtime_host"] = "localhost"
    with pytest.raises(voice_config.ConfigError, match="localhost"):
        voice_config.validate_manifest(data)


def test_rejects_non_canonical_loopback():
    data = _valid_manifest()
    data["voice"]["stt"]["base_url"] = "http://127.0.0.2:8090/v1"
    with pytest.raises(voice_config.ConfigError, match="127.0.0.1"):
        voice_config.validate_manifest(data)


def test_rejects_0000_and_ipv6_loopback():
    for bad in ("http://0.0.0.0:8090/v1", "http://[::1]:8090/v1"):
        data = _valid_manifest()
        data["voice"]["stt"]["base_url"] = bad
        with pytest.raises(voice_config.ConfigError):
            voice_config.validate_manifest(data)


def test_accepts_non_loopback_remote_host():
    # STT/TTS may legitimately live on a different tailnet/LAN host.
    data = _valid_manifest()
    data["voice"]["stt"]["base_url"] = "http://100.87.34.66:8090/v1"
    voice_config.validate_manifest(data)  # should not raise


def test_rejects_url_embedded_credentials():
    data = _valid_manifest()
    data["voice"]["llm"]["base_url"] = "http://user:pass@127.0.0.1:8000/v1"
    with pytest.raises(voice_config.ConfigError, match="credentials"):
        voice_config.validate_manifest(data)


@pytest.mark.parametrize("suffix", ["?debug=true", "#fragment"])
def test_rejects_url_query_or_fragment(suffix):
    data = _valid_manifest()
    data["voice"]["llm"]["base_url"] = "http://127.0.0.1:8000/v1" + suffix
    with pytest.raises(voice_config.ConfigError, match="query strings or fragments"):
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


def test_rejects_realtime_token_literal():
    """Q2 hardening: a plain `realtime_token = "..."` literal must be
    rejected the same way `api_key`/`token`/`secret`/`password` are --
    forcing `realtime_token_env` instead."""
    data = _valid_manifest()
    data["voice"]["realtime_token"] = "whatever-value"
    with pytest.raises(voice_config.ConfigError, match="env var name"):
        voice_config.validate_manifest(data)


def test_rejects_malformed_env_var_name():
    data = _valid_manifest()
    data["voice"]["llm"]["api_key_env"] = "not a valid env name!"
    with pytest.raises(voice_config.ConfigError, match="ENV_VAR_NAME"):
        voice_config.validate_manifest(data)


def test_rejects_invalid_audio_lifecycle():
    data = _valid_manifest()
    data["voice"]["stt"]["lifecycle"] = "sidecar"
    with pytest.raises(voice_config.ConfigError, match="lifecycle"):
        voice_config.validate_manifest(data)


def test_native_audio_lifecycle_requires_start_command():
    data = _valid_manifest()
    data["voice"]["stt"]["lifecycle"] = "native"
    with pytest.raises(voice_config.ConfigError, match="start_command"):
        voice_config.validate_manifest(data)


def test_native_audio_lifecycle_requires_same_host_base_url():
    data = _valid_manifest()
    data["voice"]["stt"].update({
        "base_url": "http://100.87.34.66:30010/v1",
        "lifecycle": "native",
        "start_command": "python -m mlx_audio.server --port 30010",
    })
    with pytest.raises(voice_config.ConfigError, match="same-host"):
        voice_config.validate_manifest(data)


def test_native_audio_lifecycle_rejects_secret_literal_in_command():
    data = _valid_manifest()
    data["voice"]["stt"].update({
        "lifecycle": "native",
        "start_command": "python -m mlx_audio.server --token sk-proj-secret12345",
    })
    with pytest.raises(voice_config.ConfigError, match="secret literal"):
        voice_config.validate_manifest(data)


def test_external_audio_lifecycle_rejects_native_process_keys():
    data = _valid_manifest()
    data["voice"]["stt"]["lifecycle"] = "external"
    data["voice"]["stt"]["start_command"] = "python -m mlx_audio.server"
    with pytest.raises(voice_config.ConfigError, match="native process keys"):
        voice_config.validate_manifest(data)


def test_accepts_native_audio_lifecycle_with_process_metadata():
    data = _valid_manifest()
    data["voice"]["stt"].update({
        "lifecycle": "native",
        "start_command": "python -m mlx_audio.server --host 127.0.0.1 --port 8090",
        "stop_command": "pkill -f 'mlx_audio.server.*--port 8090'",
        "workdir": "~/code/mlx-audio",
        "pid_file": "/tmp/anvil-voice/stt.pid",
        "log_file": "/tmp/anvil-voice/stt.log",
        "ready_timeout": 60.0,
        "stop_timeout": 5.0,
    })
    voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_timeout():
    data = _valid_manifest()
    data["voice"]["llm"]["timeout"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_max_tokens():
    data = _valid_manifest()
    data["voice"]["llm"]["max_tokens"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_negative_llm_temperature():
    data = _valid_manifest()
    data["voice"]["llm"]["temperature"] = -0.1
    with pytest.raises(voice_config.ConfigError, match="nonnegative"):
        voice_config.validate_manifest(data)


def test_accepts_llm_history_limits():
    data = _valid_manifest()
    data["voice"]["llm"]["history_max_turns"] = 0
    data["voice"]["llm"]["history_max_message_chars"] = 128
    data["voice"]["llm"]["tool_result_max_chars"] = 4096
    data["voice"]["llm"]["speech_chunk_max_chars"] = 72
    voice_config.validate_manifest(data)


def test_rejects_negative_llm_history_max_turns():
    data = _valid_manifest()
    data["voice"]["llm"]["history_max_turns"] = -1
    with pytest.raises(voice_config.ConfigError, match="nonnegative"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_history_max_message_chars():
    data = _valid_manifest()
    data["voice"]["llm"]["history_max_message_chars"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_tool_result_timeout():
    data = _valid_manifest()
    data["voice"]["llm"]["tool_result_timeout"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_negative_llm_tool_call_max_rounds():
    data = _valid_manifest()
    data["voice"]["llm"]["tool_call_max_rounds"] = -1
    with pytest.raises(voice_config.ConfigError, match="nonnegative"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_tool_result_max_chars():
    data = _valid_manifest()
    data["voice"]["llm"]["tool_result_max_chars"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_llm_speech_chunk_max_chars():
    data = _valid_manifest()
    data["voice"]["llm"]["speech_chunk_max_chars"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_non_integer_llm_speech_chunk_max_chars():
    data = _valid_manifest()
    data["voice"]["llm"]["speech_chunk_max_chars"] = "fast"
    with pytest.raises(voice_config.ConfigError, match="integer"):
        voice_config.validate_manifest(data)


def test_rejects_empty_llm_system_prompt():
    data = _valid_manifest()
    data["voice"]["llm"]["system_prompt"] = ""
    with pytest.raises(voice_config.ConfigError, match="system_prompt"):
        voice_config.validate_manifest(data)


def test_rejects_stt_response_format_not_consumed_by_client():
    data = _valid_manifest()
    data["voice"]["stt"]["response_format"] = "text"
    with pytest.raises(voice_config.ConfigError, match="response_format"):
        voice_config.validate_manifest(data)


def test_rejects_tts_container_response_format():
    data = _valid_manifest()
    data["voice"]["tts"]["response_format"] = "wav"
    with pytest.raises(voice_config.ConfigError, match="response_format"):
        voice_config.validate_manifest(data)


def test_rejects_nonpositive_tts_sample_rate():
    data = _valid_manifest()
    data["voice"]["tts"]["source_sample_rate"] = 0
    with pytest.raises(voice_config.ConfigError, match="positive"):
        voice_config.validate_manifest(data)


def test_rejects_non_loopback_realtime_host_without_token_env():
    """U2-a: defense in depth. `realtime.ws.make_ws_server`'s own F2 guard
    already refuses to BIND a non-loopback host with no token, but that only
    protects a caller that actually reaches `make_ws_server` -- the manifest
    itself must reject this combination too, so it can never validate as
    "OK" in the first place."""
    data = _valid_manifest()
    data["voice"]["realtime_host"] = "100.87.34.66"
    with pytest.raises(voice_config.ConfigError, match="realtime_token_env"):
        voice_config.validate_manifest(data)


def test_accepts_non_loopback_realtime_host_with_token_env():
    data = _valid_manifest()
    data["voice"]["realtime_host"] = "100.87.34.66"
    data["voice"]["realtime_token_env"] = "ANVIL_VOICE_REALTIME_TOKEN"
    voice_config.validate_manifest(data)  # should not raise


def test_accepts_loopback_realtime_host_without_token_env():
    data = _valid_manifest()
    data["voice"]["realtime_host"] = "127.0.0.1"
    assert "realtime_token_env" not in data["voice"]
    voice_config.validate_manifest(data)  # trusted-local default: should not raise


def test_missing_section_raises():
    data = _valid_manifest()
    del data["voice"]["stt"]
    with pytest.raises(voice_config.ConfigError, match=r"voice\.stt"):
        voice_config.validate_manifest(data)


def test_missing_required_field_raises():
    data = _valid_manifest()
    del data["voice"]["llm"]["model"]
    with pytest.raises(voice_config.ConfigError, match="model"):
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


def test_load_manifest_missing_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(voice_config.ConfigError, match="not found"):
        voice_config.load_manifest(str(missing))


def test_load_manifest_bad_toml_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not [valid toml", encoding="utf-8")
    with pytest.raises(voice_config.ConfigError, match="cannot parse"):
        voice_config.load_manifest(str(bad))


def test_load_manifest_from_tmp_path_roundtrip(tmp_path):
    manifest = tmp_path / "voice.toml"
    manifest.write_text(
        """
[voice]
name = "test-voice"
realtime_host = "127.0.0.1"
realtime_port = 9999

[voice.llm]
base_url = "http://127.0.0.1:8000/v1"
model = "chat"

[voice.stt]
base_url = "http://127.0.0.1:8090/v1"
model = "whisper-tiny"

[voice.tts]
base_url = "http://127.0.0.1:8091/v1"
model = "kokoro-82m"
""".strip(),
        encoding="utf-8",
    )
    data = voice_config.load_manifest(str(manifest))
    assert data["voice"]["name"] == "test-voice"
    assert data["voice"]["realtime_port"] == 9999
