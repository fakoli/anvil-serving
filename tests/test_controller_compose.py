"""Static security and packaging checks for the controller deployment."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "examples" / "fakoli-dark" / "docker-compose.controller.yml"


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_controller_compose_is_hardened_and_loopback_published():
    text = _text()

    assert "target: controller" in text
    assert "${ANVIL_CONTROLLER_PUBLISH:-127.0.0.1}:8765:8765" in text
    assert "read_only: true" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop:" in text and "- ALL" in text
    assert "/var/run/docker.sock:/var/run/docker.sock" in text
    assert "group_add:" in text and '- "0"' in text
    assert "gpus: all" in text
    assert "host.docker.internal:host-gateway" in text
    assert "ANVIL_COMMAND_HOST: host:fakoli-dark" in text
    assert "ANVIL_COMMAND_RUNTIME: runtime:dark-docker" in text


def test_controller_compose_mounts_explicit_artifacts_not_operator_homes():
    text = _text()

    assert "~/.anvil-serving" not in text
    assert "/.ssh" not in text
    assert "/.config/gh" not in text
    assert ":/etc/anvil:ro" not in text
    assert "./serves.toml:/etc/anvil/serves.toml:ro" in text
    assert "./operator-topology.toml:/etc/anvil/operator-topology.toml:ro" in text
    assert "./docker-compose.yml:/etc/anvil/docker-compose.yml:ro" in text


def test_controller_compose_excludes_native_host_and_gateway_tools():
    text = _text()

    for excluded in (
        "host_manage",
        "host_summary",
        "doctor_summary",
        "voice_proxy_manage",
        "openclaw_gateway_restart",
        "openclaw_gateway_status",
    ):
        assert f"- {excluded}\n" not in text
