"""Static least-authority checks for the media lifecycle controller."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "examples" / "fakoli-dark" / "docker-compose.media-controller.yml"


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_media_controller_is_loopback_only_and_shares_host_local_state():
    text = _text()

    assert "anvil-serving-controller:0.36.0" in text
    assert "${ANVIL_MEDIA_CONTROLLER_PUBLISH:-127.0.0.1:18765}:8765" in text
    assert "./media-state:/var/lib/anvil-media" in text
    assert "ANVIL_MEDIA_STATE_DB: /var/lib/anvil-media/media-jobs.sqlite3" in text
    assert "ANVIL_MEDIA_RESOURCE_CONTROLLER_URL" in text
    assert "ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN" in text
    assert "read_only: true" in text
    assert "no-new-privileges:true" in text


def test_media_controller_has_exactly_the_four_lifecycle_operations():
    text = _text()
    expected = {
        "media_worker_prepare",
        "media_worker_status",
        "media_worker_logs",
        "media_worker_teardown",
    }
    observed = {
        line.strip()[2:]
        for line in text.splitlines()
        if line.strip().startswith("- media_")
    }

    assert observed == expected
    assert "/var/run/docker.sock" not in text
    assert "serves_manage" not in text
    assert "router_manage" not in text
