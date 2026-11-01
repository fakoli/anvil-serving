from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "huggingface-speech-to-speech"
README = EXAMPLE_DIR / "README.md"
GATEWAY_EXAMPLE = EXAMPLE_DIR / "openclaw-gateway.example.toml"


def _example_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in (README, GATEWAY_EXAMPLE)
    )


def test_example_avoids_loopback_alias_and_literal_secrets():
    text = _example_text()
    forbidden_host = "local" + "host"
    assert forbidden_host not in text.lower()
    assert ("sk" + "-") not in text
    assert ("hf" + "_") not in text
    assert "api_key_env = \"ANVIL_ROUTER_TOKEN\"" in text
    assert "container_image = \"speech-to-speech:local\"" in text
    assert "container_base_url = \"http://host.docker.internal:8000/v1\"" in text
