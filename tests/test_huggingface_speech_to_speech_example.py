from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "huggingface-speech-to-speech"
README = EXAMPLE_DIR / "README.md"
GATEWAY_EXAMPLE = EXAMPLE_DIR / "openclaw-gateway.example.toml"


def _example_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in (README, GATEWAY_EXAMPLE)
    )


def test_example_files_exist():
    assert README.is_file()
    assert GATEWAY_EXAMPLE.is_file()


def test_example_uses_chat_completions_backend_and_anvil_router():
    text = _example_text()
    assert "--llm_backend chat-completions" in text
    assert '--responses_api_base_url "http://127.0.0.1:8000/v1"' in text
    assert "--model_name chat" in text
    assert 'base_url = "http://127.0.0.1:8000/v1"' in text
    assert "anvil-serving voice-sidecar command" in text
    assert "anvil-serving voice-sidecar validate" in text
    assert "anvil-serving voice-sidecar compose" in text


def test_example_keeps_realtime_sidecar_outside_router():
    text = _example_text()
    assert "ws://127.0.0.1:8765/v1/realtime" in text
    assert "Anvil does not implement `/v1/realtime`" in text
    assert "Connect your Realtime client to `speech-to-speech`, not anvil" in text


def test_example_documents_openclaw_gateway_boundary():
    text = _example_text()
    assert "The Gateway remains the\nphone-facing WebSocket contract" in text
    assert 'phone_facing_owner = "openclaw-gateway"' in text
    assert "ios_protocol_change_required = false" in text
    assert "voice-sidecar.tailnet.example" in text


def test_example_documents_16gb_validation_checklist():
    text = _example_text()
    assert "16 GB shared memory" in text
    assert "LLM: `--model_name chat` through anvil-serving" in text
    assert "Fully local STT plus TTS plus a small GGUF LLM" in text
    for phrase in (
        "Idle memory",
        "Memory after STT/TTS model load",
        "Startup time",
        "First audio response latency",
        "Failure mode",
    ):
        assert phrase in text


def test_example_avoids_loopback_alias_and_literal_secrets():
    text = _example_text()
    forbidden_host = "local" + "host"
    assert forbidden_host not in text.lower()
    assert ("sk" + "-") not in text
    assert ("hf" + "_") not in text
    assert "api_key_env = \"ANVIL_ROUTER_TOKEN\"" in text
    assert "container_image = \"speech-to-speech:local\"" in text
    assert "container_base_url = \"http://host.docker.internal:8000/v1\"" in text
