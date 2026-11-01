"""Daemonless lint of the repo-root `Dockerfile` + `.dockerignore` (router-service:T002,
ADR-0004). Asserts invariants by parsing the file text -- no Docker daemon, no build,
no network required, so this runs in plain CI.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_never_hardcodes_a_secret_looking_literal():
    text = _text()
    # Defense-in-depth: no ANVIL_ROUTER_TOKEN value baked into the image build.
    assert "ANVIL_ROUTER_TOKEN=" not in text.replace(" ", "")
