import json
import shlex
from pathlib import Path

import pytest

from anvil_serving import cli
from anvil_serving import voice_sidecar


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "huggingface-speech-to-speech" / "openclaw-gateway.example.toml"
VOICE_OPS_SKILL = ROOT / "skills" / "anvil-serving-voice-ops" / "SKILL.md"
OPERATOR_DOC = ROOT / "docs" / "OPERATOR-SKILLS-AND-SUBAGENTS.md"


def _manifest():
    return voice_sidecar.load_manifest(str(EXAMPLE))


def test_command_can_include_auth_for_authenticated_router():
    argv = voice_sidecar.command_args(_manifest(), include_auth=True)
    command = voice_sidecar.shell_command(argv)
    assert argv[argv.index("--responses_api_api_key") + 1] == "$ANVIL_ROUTER_TOKEN"
    assert '"$ANVIL_ROUTER_TOKEN"' in command


def test_compose_service_can_include_auth_with_explicit_warning():
    text = voice_sidecar.compose_service(_manifest(), include_auth=True)
    assert '${ANVIL_ROUTER_TOKEN}' in text
    assert "process argv at runtime" in text


def test_validate_rejects_secret_literals():
    data = _manifest()
    data["voice_sidecar"]["llm_backend"]["api_key"] = "sk" + "-test-secret"
    with pytest.raises(voice_sidecar.ConfigError, match="env var name"):
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


def test_cli_dispatches_voice_sidecar_command_json_with_auth(capsys):
    rc = cli.main([
        "voice",
        "sidecar",
        "command",
        "--config",
        str(EXAMPLE),
        "--with-auth",
        "--json",
    ])
    assert rc == 0
    envelope = json.loads(capsys.readouterr().out)
    argv = shlex.split(envelope["data"], posix=False)
    assert argv[argv.index("--responses_api_api_key") + 1].strip('"') == "$ANVIL_ROUTER_TOKEN"


def test_voice_ops_skill_uses_existing_verbs_and_scopes_evidence():
    text = VOICE_OPS_SKILL.read_text(encoding="utf-8")
    for command in [
        "anvil-serving voice sidecar validate",
        "anvil-serving voice sidecar command",
        "anvil-serving voice sidecar compose",
        "anvil-serving voice audio up",
        "voice audio down",
        "voice audio status",
        "voice proxy up",
        "voice proxy run",
        "voice proxy bridge",
        "anvil-serving voice benchmark",
    ]:
        assert command in text
    assert "--topology <topology>" in text
    assert "voice-pipeline evidence" in text
    assert "LLM serve qualification" in text
    assert "promotion_quality_evidence: false" in text
    assert "promoted=false" in text
    assert "127.0.0.1" in text
    assert "router_promote" not in text

    docs = OPERATOR_DOC.read_text(encoding="utf-8")
    assert "`skills/anvil-serving-voice-ops/SKILL.md`" in docs
    assert "Voice benchmark output is" in docs
    assert "not LLM serve qualification evidence" in docs
    assert "`promotion_quality_evidence` remains `false`" in docs
