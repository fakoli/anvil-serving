from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from anvil_serving import router_endpoint


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _docker_inspect(*, host="127.0.0.1", port="8000", running=True):
    return json.dumps(
        [
            {
                "State": {"Running": running},
                "NetworkSettings": {
                    "Ports": {"8000/tcp": [{"HostIp": host, "HostPort": port}]}
                },
            }
        ]
    )


def test_discovery_reads_live_docker_binding_and_magicdns():
    def run(argv, **_kwargs):
        assert argv == ["docker", "inspect", "anvil-router"]
        return _completed(stdout=_docker_inspect(host="100.64.0.10", port="8765"))

    result = router_endpoint.discover_router_endpoint(
        run=run,
        read_magicdns=lambda: ("node-a.example.ts.net", "connected"),
    )

    assert result.listen_host == "100.64.0.10"
    assert result.listen_port == 8765
    assert result.local_url == "http://100.64.0.10:8765"
    assert result.source == "docker:anvil-router"
    assert result.router_running is True
    assert result.tailscale_dns_name == "node-a.example.ts.net"


def test_find_tailscale_cli_finds_windows_install_location():
    expected = Path("C:/Program Files/Tailscale/tailscale.exe")
    found = router_endpoint.find_tailscale_cli(
        platform="win32",
        env={"ProgramFiles": "C:/Program Files"},
        which=lambda _name: None,
        is_file=lambda path: Path(path) == expected,
    )
    assert Path(found) == expected


def test_cli_renders_endpoint(capsys, monkeypatch):
    result = router_endpoint.RouterEndpoint(
        listen_host="127.0.0.1",
        listen_port=8000,
        local_url="http://127.0.0.1:8000",
        source="default",
        container="anvil-router",
        router_running=None,
        tailscale_dns_name="node.example.ts.net",
        tailscale_status="connected",
    )
    monkeypatch.setattr(router_endpoint, "discover_router_endpoint", lambda **_kwargs: result)

    assert router_endpoint.main([]) == 0
    output = capsys.readouterr().out
    assert "listen:        127.0.0.1:8000" in output
    assert "Tailscale DNS: node.example.ts.net" in output
