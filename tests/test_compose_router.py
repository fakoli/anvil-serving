"""Daemonless checks for the primary-node router+serves compose topology
(router-service:T003, ADR-0004). Asserts invariants by parsing the compose YAML text
with a small indentation-based block splitter -- no Docker daemon, no `docker compose`
invocation, and no PyYAML dependency (stdlib-only), so this runs in plain CI even
though the dev box happens to have Docker installed.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "examples" / "primary-node" / "docker-compose.yml"
EXPERIMENT_COMPOSE_PATH = REPO_ROOT / "examples" / "primary-node" / "docker-compose.experiment.yml"
DOCKER_CONFIG_PATH = REPO_ROOT / "configs" / "example-docker.toml"
FLEXIBILITY_COMPOSE_PATH = REPO_ROOT / "examples" / "primary-node" / "docker-compose.flexibility.yml"
VLLM_STABLE_IMAGE = (
    "vllm/vllm-openai@sha256:"
    "e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089"
)


def _compose_text() -> str:
    return COMPOSE_PATH.read_text(encoding="utf-8")


def _service_blocks(text: str) -> dict[str, str]:
    """Split the ``services:`` section into ``{service_name: block_text}``.

    Assumes the conventional 2-space top-level-service / no-tabs Compose style this
    repo already uses (verified by grep before writing this parser).
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "services:")
    except StopIteration:
        raise AssertionError("compose file has no top-level `services:` key")

    service_re = re.compile(r"^  ([A-Za-z0-9_.-]+):\s*$")
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break  # dedented past the services: section (e.g. a top-level `networks:`)
        m = service_re.match(line)
        if m:
            current = m.group(1)
            blocks[current] = []
            continue
        if current is not None:
            blocks[current].append(line)
    return {name: "\n".join(body) for name, body in blocks.items()}


def _port_host_binds(block: str) -> list[str]:
    """Return the host-bind portion of every short-syntax `ports:` entry in a block.

    e.g. ``- "127.0.0.1:30000:30000"`` -> ``"127.0.0.1"``; a bare ``"8000:8000"``
    (no host IP given, i.e. published on ALL interfaces) -> ``"0.0.0.0"``. An
    env-overridable bind ``- "${FOO_PUBLISH:-127.0.0.1}:30005:30005"`` normalizes
    to its DEFAULT (``"127.0.0.1"``): what ships is the default, and the security
    property under test is that the shipped default stays loopback — the env var
    is the operator's explicit, per-deploy opt-in beyond it (ADR-0004).
    """
    binds = []
    for m in re.finditer(r'-\s*"?\$?\{?([^"\s{}:]*)(?::-([^}]*)\})?\}?:(\d+):(\d+)"?', block):
        host_ip, default = m.group(1), m.group(2)
        if default is not None:
            binds.append(default if default else "0.0.0.0")
        else:
            binds.append(host_ip if host_ip else "0.0.0.0")
    return binds


def test_example_docker_toml_sets_server_auth_env():
    text = DOCKER_CONFIG_PATH.read_text(encoding="utf-8")
    assert re.search(r'^\[server\]\s*$', text, re.MULTILINE), "expected a [server] table"
    assert re.search(r'auth_env\s*=\s*"ANVIL_ROUTER_TOKEN"', text)
