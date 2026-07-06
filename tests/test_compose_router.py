"""Daemonless checks for the fakoli-dark router+serves compose topology
(router-service:T003, ADR-0004). Asserts invariants by parsing the compose YAML text
with a small indentation-based block splitter -- no Docker daemon, no `docker compose`
invocation, and no PyYAML dependency (stdlib-only), so this runs in plain CI even
though the dev box happens to have Docker installed.
"""
from __future__ import annotations

import io
import pathlib
import re
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "examples" / "fakoli-dark" / "docker-compose.yml"
DOCKER_CONFIG_PATH = REPO_ROOT / "configs" / "example-docker.toml"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


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
    (no host IP given, i.e. published on ALL interfaces) -> ``"0.0.0.0"``.
    """
    binds = []
    for m in re.finditer(r'-\s*"?\$?\{?([^"\s{}:]*)(?::-[^}]*\})?\}?:(\d+):(\d+)"?', block):
        host_ip = m.group(1)
        binds.append(host_ip if host_ip else "0.0.0.0")
    return binds


def test_compose_file_exists():
    assert COMPOSE_PATH.is_file()


def test_router_service_present():
    services = _service_blocks(_compose_text())
    assert "router" in services, f"expected a `router` service, got: {sorted(services)}"


def test_router_service_restart_unless_stopped():
    services = _service_blocks(_compose_text())
    router = services["router"]
    assert re.search(r"^\s*restart:\s*unless-stopped\s*$", router, re.MULTILINE), (
        "router service must set `restart: unless-stopped`"
    )


def test_router_service_pins_the_deployed_image():
    # 2026-07-04 flexibility reconcile: the compose reproduces the LIVE deployment, which
    # runs a PINNED image (not a fresh build). Pinning is load-bearing: a `build:` here
    # would produce a newer (v2-schema) router that rejects the live v1 profile.json, and
    # it would diverge from what is actually deployed. Redeploying to a freshly-built image
    # (to get flexibility-mode/v2) is a separate, deliberate step — not what `serves`/compose
    # does implicitly. So the router service must pin an image and must NOT build.
    services = _service_blocks(_compose_text())
    router = services["router"]
    assert re.search(r"^\s*image:\s*anvil-serving:", router, re.MULTILINE), (
        "router service must pin the deployed anvil-serving image"
    )
    assert "build:" not in router, (
        "router service must NOT build (a fresh build produces a v2 image that rejects the "
        "live v1 profile); redeploying to a built image is a deliberate separate step"
    )


def test_router_image_pin_matches_package_version():
    services = _service_blocks(_compose_text())
    router = services["router"]
    version = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["version"]
    expected = f"anvil-serving:{version}"
    assert re.search(rf"^\s*image:\s*{re.escape(expected)}\s*$", router, re.MULTILINE), (
        f"router service image must track the source package version {expected!r}"
    )


def test_router_service_passes_token_from_environment():
    services = _service_blocks(_compose_text())
    router = services["router"]
    assert "ANVIL_ROUTER_TOKEN" in router, (
        "router service must pass ANVIL_ROUTER_TOKEN through from the environment"
    )
    # Never a hardcoded secret literal -- only an env-var passthrough/default.
    assert not re.search(r"ANVIL_ROUTER_TOKEN:\s*[A-Za-z0-9+/_-]{16,}\s*$", router, re.MULTILINE), (
        "ANVIL_ROUTER_TOKEN must be passed through from the environment, never hardcoded"
    )


def test_router_service_mounts_the_config_volume():
    # The router reads config + profile from the anvil-router-cfg VOLUME at /etc/anvil
    # (mounted read-only). This replaced the old repo bind-mount because `anvil-serving
    # router promote` writes the promoted profile/config INTO that volume out-of-band — a
    # read-only repo bind-mount cannot be a promotion target.
    services = _service_blocks(_compose_text())
    router = services["router"]
    assert re.search(r'anvil-router-cfg:/etc/anvil(:ro)?', router), (
        "router service must mount the anvil-router-cfg volume at /etc/anvil (the promote target)"
    )


def test_only_router_is_published_beyond_loopback():
    services = _service_blocks(_compose_text())
    for name, block in services.items():
        for host_ip in _port_host_binds(block):
            if name == "router":
                continue
            assert host_ip in ("127.0.0.1", "${ROUTER_PUBLISH:-127.0.0.1}"), (
                f"service {name!r} publishes a port on {host_ip!r} (must stay "
                f"loopback-only or unpublished; the router is the only service "
                f"allowed beyond loopback -- ADR-0004)"
            )


def test_router_port_defaults_to_loopback_but_is_overridable():
    services = _service_blocks(_compose_text())
    router = services["router"]
    host_binds = _port_host_binds(router)
    assert host_binds, "router service must publish a port"
    for host_ip in host_binds:
        # Must be the overridable ROUTER_PUBLISH var (defaulting to loopback), never a
        # bare 0.0.0.0 wildcard baked in.
        assert host_ip != "0.0.0.0", "router port must not hardcode a 0.0.0.0 publish"


def test_serves_reached_by_router_via_host_gateway_not_loopback():
    # configs/example-docker.toml is what the router container actually loads;
    # its tiers must address the host-published loopback serves through
    # host.docker.internal. 127.0.0.1 would mean "inside the router container".
    text = DOCKER_CONFIG_PATH.read_text(encoding="utf-8")
    assert "http://host.docker.internal:30002/v1" in text
    assert "http://host.docker.internal:30003/v1" in text
    assert "http://sglang:30000/v1" not in text
    assert "http://fast:30001/v1" not in text
    assert "127.0.0.1:30002" not in text
    assert "127.0.0.1:30003" not in text


def test_example_docker_toml_sets_server_auth_env():
    text = DOCKER_CONFIG_PATH.read_text(encoding="utf-8")
    assert re.search(r'^\[server\]\s*$', text, re.MULTILINE), "expected a [server] table"
    assert re.search(r'auth_env\s*=\s*"ANVIL_ROUTER_TOKEN"', text)


def test_example_docker_toml_loads_via_router_config_with_no_missing_model_warning():
    from anvil_serving.router.config import load

    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        config = load(str(DOCKER_CONFIG_PATH))
    finally:
        sys.stderr = old_stderr

    assert config.tiers, "expected at least one tier to load"
    stderr_output = captured.getvalue()
    assert "no `model` set" not in stderr_output, (
        f"expected no missing-model WARNING, got stderr: {stderr_output!r}"
    )
    assert "WARNING" not in stderr_output, f"expected clean load, got stderr: {stderr_output!r}"
