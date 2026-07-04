import copy
import json
from pathlib import Path

import pytest

from anvil_serving import cli
from anvil_serving import voice_sidecar


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "huggingface-speech-to-speech" / "openclaw-gateway.example.toml"


def _manifest():
    return voice_sidecar.load_manifest(str(EXAMPLE))


def test_example_manifest_loads_and_renders_host_command():
    data = _manifest()
    argv = voice_sidecar.command_args(data)
    command = voice_sidecar.shell_command(argv)

    assert argv[:2] == ["speech-to-speech", "--mode"]
    assert "--llm_backend" in argv
    assert argv[argv.index("--llm_backend") + 1] == "chat-completions"
    assert argv[argv.index("--responses_api_base_url") + 1] == "http://127.0.0.1:8000/v1"
    assert argv[argv.index("--model_name") + 1] == "chat"
    assert argv[argv.index("--responses_api_api_key") + 1] == "$ANVIL_ROUTER_TOKEN"
    assert '"$ANVIL_ROUTER_TOKEN"' in command
    assert "ws://127.0.0.1:8765/v1/realtime" not in command


def test_command_can_omit_auth_for_unauthenticated_router():
    argv = voice_sidecar.command_args(_manifest(), include_auth=False)
    assert "--responses_api_api_key" not in argv


def test_compose_service_uses_loopback_port_and_env_reference_only():
    text = voice_sidecar.compose_service(_manifest())
    assert "Replace speech-to-speech:local with the image you build or publish" in text
    assert "image: speech-to-speech:local" in text
    assert '"127.0.0.1:8765:8765"' in text
    assert '${ANVIL_ROUTER_TOKEN}' in text
    assert "sk-" not in text
    assert "hf_" not in text


def test_validate_rejects_non_chat_completions_backend():
    data = _manifest()
    data["voice_sidecar"]["llm_backend"]["backend"] = "responses-api"
    with pytest.raises(voice_sidecar.ConfigError, match="chat-completions"):
        voice_sidecar.validate_manifest(data)


def test_validate_rejects_secret_literals():
    data = _manifest()
    data["voice_sidecar"]["llm_backend"]["api_key"] = "sk-test-secret"
    with pytest.raises(voice_sidecar.ConfigError, match="env var name"):
        voice_sidecar.validate_manifest(data)


def test_validate_rejects_loopback_name_that_is_not_127001():
    data = _manifest()
    data["voice_sidecar"]["llm_backend"]["base_url"] = (
        "http://" + "local" + "host" + ":8000/v1"
    )
    with pytest.raises(voice_sidecar.ConfigError, match="127.0.0.1"):
        voice_sidecar.validate_manifest(data)


def test_validate_rejects_realtime_url_that_does_not_point_at_realtime_path():
    data = _manifest()
    data["voice_sidecar"]["same_host_realtime_url"] = "ws://127.0.0.1:8765/v1/chat/completions"
    with pytest.raises(voice_sidecar.ConfigError, match="/v1/realtime"):
        voice_sidecar.validate_manifest(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_url", "http://" + "user:pass@" + "127.0.0.1:8000/v1"),
        ("same_host_realtime_url", "ws://" + "user:pass@" + "127.0.0.1:8765/v1/realtime"),
    ],
)
def test_validate_rejects_urls_with_embedded_credentials(field, value):
    data = _manifest()
    target = data["voice_sidecar"]["llm_backend"] if field == "base_url" else data["voice_sidecar"]
    target[field] = value
    with pytest.raises(voice_sidecar.ConfigError, match="must not embed credentials"):
        voice_sidecar.validate_manifest(data)


def test_validate_rejects_bad_env_var_name():
    data = _manifest()
    data["voice_sidecar"]["llm_backend"]["api_key_env"] = "not-loud-enough"
    with pytest.raises(voice_sidecar.ConfigError, match="env var name"):
        voice_sidecar.validate_manifest(data)


def test_cli_dispatches_voice_sidecar_validate(capsys):
    rc = cli.main(["voice-sidecar", "validate", "--config", str(EXAMPLE)])
    assert rc == 0
    assert "OK:" in capsys.readouterr().out


def test_cli_uses_source_checkout_default_config(capsys):
    rc = cli.main(["voice-sidecar", "validate"])
    assert rc == 0
    assert str(EXAMPLE) in capsys.readouterr().out


def test_cli_without_config_explains_missing_installed_default(monkeypatch, capsys, tmp_path):
    missing = tmp_path / "missing-example.toml"
    monkeypatch.setattr(voice_sidecar, "DEFAULT_CONFIG", str(missing))

    rc = voice_sidecar.main(["validate"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "pass --config PATH" in err
    assert "config not found" not in err


def test_cli_dispatches_voice_sidecar_command_json(capsys):
    rc = cli.main(["voice-sidecar", "command", "--config", str(EXAMPLE), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["argv"][0] == "speech-to-speech"
    assert "--llm_backend" in payload["argv"]


def test_manifest_validation_is_pure():
    data = _manifest()
    before = copy.deepcopy(data)
    voice_sidecar.validate_manifest(data)
    assert data == before
