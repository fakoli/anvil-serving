"""Static least-authority checks for the media lifecycle controller."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "examples" / "primary-node" / "docker-compose.media-controller.yml"
TOPOLOGY = REPO / "examples" / "primary-node" / "operator-topology.toml"
SCAFFOLD_TOPOLOGY = (
    REPO / "anvil_serving" / "_scaffold_templates" / "operator-topology.toml"
)


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_media_controller_uses_distinct_edge_credentials():
    text = _text()
    assert (
        "ANVIL_MEDIA_CONTROLLER_TOKEN: "
        "${ANVIL_MEDIA_CONTROLLER_TOKEN:?set ANVIL_MEDIA_CONTROLLER_TOKEN}"
    ) in text
    assert "ANVIL_CONTROLLER_TOKEN:" not in text
    assert text.count("- ANVIL_MEDIA_CONTROLLER_TOKEN") == 2
    assert (
        "ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN: "
        "${ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN:"
        "?set ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN}"
    ) in text

    topology = TOPOLOGY.read_text(encoding="utf-8")
    media_transport = topology.split('id = "media-controller"', 1)[1].split(
        "[[transports]]", 1
    )[0]
    assert 'auth_env = "ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN"' in media_transport
    assert TOPOLOGY.read_bytes() == SCAFFOLD_TOPOLOGY.read_bytes()
