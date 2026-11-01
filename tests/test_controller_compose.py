"""Static security and packaging checks for the controller deployment."""

from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "examples" / "primary-node" / "docker-compose.controller.yml"
MANIFEST = REPO / "examples" / "primary-node" / "serves.toml"


def _text() -> str:
    return COMPOSE.read_text(encoding="utf-8")
