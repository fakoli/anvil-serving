"""Discover the deployed router listen address and this node's MagicDNS name."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Mapping, Sequence

from .edge import resolve_magicdns_name


DEFAULT_CONTAINER = "anvil-router"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class RouterEndpoint:
    """One bounded endpoint-discovery result."""

    listen_host: str
    listen_port: int
    local_url: str
    source: str
    container: str
    router_running: bool | None
    tailscale_dns_name: str | None
    tailscale_status: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _ContainerBinding:
    host: str | None
    port: int | None
    running: bool


def _inspect_container_binding(
    container: str,
    *,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> _ContainerBinding | None:
    """Read the router's published Docker port without shell/platform parsing."""
    try:
        completed = run(
            ["docker", "inspect", container],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    try:
        documents = json.loads((getattr(completed, "stdout", "") or "").strip())
        document = documents[0]
    except (IndexError, KeyError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(document, Mapping):
        return None
    state = document.get("State")
    running = bool(state.get("Running")) if isinstance(state, Mapping) else False
    network = document.get("NetworkSettings")
    ports = network.get("Ports") if isinstance(network, Mapping) else None
    if not isinstance(ports, Mapping):
        return _ContainerBinding(None, None, running)

    raw_bindings = ports.get("8000/tcp")
    if not isinstance(raw_bindings, list):
        candidates = [value for value in ports.values() if isinstance(value, list) and value]
        raw_bindings = candidates[0] if len(candidates) == 1 else []
    bindings = [item for item in raw_bindings if isinstance(item, Mapping)]
    if not bindings:
        return _ContainerBinding(None, None, running)
    # Docker commonly returns both 0.0.0.0 and ::. Prefer the IPv4 record for
    # consistent output while still supporting an IPv6-only binding.
    binding = next(
        (item for item in bindings if ":" not in str(item.get("HostIp", ""))),
        bindings[0],
    )
    host = str(binding.get("HostIp") or "0.0.0.0")
    try:
        port = int(str(binding.get("HostPort") or ""))
    except ValueError:
        port = None
    if port is not None and not 0 < port <= 65535:
        port = None
    return _ContainerBinding(host, port, running)


def find_tailscale_cli(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    which=shutil.which,
    is_file=lambda path: Path(path).is_file(),
) -> str | None:
    """Find the Tailscale CLI on Linux, macOS, and Windows."""
    found = which("tailscale")
    if found:
        return found

    platform = platform or sys.platform
    environ = os.environ if env is None else env
    candidates: list[Path] = []
    if platform == "win32":
        for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            root = environ.get(key)
            if root:
                candidates.append(Path(root) / "Tailscale" / "tailscale.exe")
    elif platform == "darwin":
        candidates.extend(
            (
                Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
                Path("/usr/local/bin/tailscale"),
                Path("/opt/homebrew/bin/tailscale"),
            )
        )
    else:
        candidates.extend((Path("/usr/bin/tailscale"), Path("/usr/local/bin/tailscale")))
    return next((str(path) for path in candidates if is_file(path)), None)


def _read_magicdns(
    *,
    run=subprocess.run,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    find_cli=find_tailscale_cli,
) -> tuple[str | None, str]:
    executable = find_cli()
    if executable is None:
        return None, "unavailable"

    def resolved_run(argv, **kwargs):
        return run([executable, *list(argv)[1:]], **kwargs)

    try:
        name = resolve_magicdns_name(run=resolved_run, timeout=timeout)
    except OSError:
        name = None
    return (name, "connected") if name else (None, "unavailable")


def _url(host: str, port: int) -> str:
    connect_host = DEFAULT_HOST if host in {"0.0.0.0", "::", "[::]"} else host
    rendered_host = f"[{connect_host}]" if ":" in connect_host and not connect_host.startswith("[") else connect_host
    return f"http://{rendered_host}:{port}"


def discover_router_endpoint(
    *,
    container: str = DEFAULT_CONTAINER,
    host: str | None = None,
    port: int | None = None,
    include_tailscale: bool = True,
    run=subprocess.run,
    read_magicdns=_read_magicdns,
) -> RouterEndpoint:
    """Return live Docker binding data plus optional MagicDNS discovery."""
    binding = _inspect_container_binding(container, run=run)
    listen_host = host or (binding.host if binding else None) or DEFAULT_HOST
    listen_port = port or (binding.port if binding else None) or DEFAULT_PORT
    if host is not None or port is not None:
        source = "override"
    elif binding is not None and binding.host is not None and binding.port is not None:
        source = f"docker:{container}"
    else:
        source = "default"

    if include_tailscale:
        dns_name, tailscale_status = read_magicdns()
    else:
        dns_name, tailscale_status = None, "skipped"
    return RouterEndpoint(
        listen_host=listen_host,
        listen_port=listen_port,
        local_url=_url(listen_host, listen_port),
        source=source,
        container=container,
        router_running=binding.running if binding is not None else None,
        tailscale_dns_name=dns_name,
        tailscale_status=tailscale_status,
    )


def _render(result: RouterEndpoint) -> str:
    running = "unknown" if result.router_running is None else ("yes" if result.router_running else "no")
    dns_name = result.tailscale_dns_name or f"unavailable ({result.tailscale_status})"
    return "\n".join(
        (
            f"listen:        {result.listen_host}:{result.listen_port}",
            f"local URL:     {result.local_url}",
            f"source:        {result.source}",
            f"router running:{running:>8}",
            f"Tailscale DNS: {dns_name}",
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="anvil-serving router endpoint",
        description="Show the router listen address/port and this node's Tailscale MagicDNS name.",
    )
    parser.add_argument("--container", default=DEFAULT_CONTAINER, help="deployed router container")
    parser.add_argument("--host", help="explicit listen host override")
    parser.add_argument("--port", type=int, help="explicit listen port override")
    parser.add_argument("--no-tailscale", action="store_true", help="skip Tailscale MagicDNS discovery")
    args = parser.parse_args(argv)
    if args.host is not None and not args.host.strip():
        parser.error("--host must not be empty")
    if args.port is not None and not 0 < args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    result = discover_router_endpoint(
        container=args.container,
        host=args.host,
        port=args.port,
        include_tailscale=not args.no_tailscale,
    )
    print(_render(result))
    return 0
