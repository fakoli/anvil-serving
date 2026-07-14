from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from anvil_serving import cli, router_endpoint


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
        return _completed(stdout=_docker_inspect(host="100.87.34.66", port="8765"))

    result = router_endpoint.discover_router_endpoint(
        run=run,
        read_magicdns=lambda: ("fakoli-dark.tail4378d.ts.net", "connected"),
    )

    assert result.listen_host == "100.87.34.66"
    assert result.listen_port == 8765
    assert result.local_url == "http://100.87.34.66:8765"
    assert result.source == "docker:anvil-router"
    assert result.router_running is True
    assert result.tailscale_dns_name == "fakoli-dark.tail4378d.ts.net"


def test_wildcard_binding_uses_loopback_for_connectable_url():
    def run(_argv, **_kwargs):
        return _completed(stdout=_docker_inspect(host="0.0.0.0"))

    result = router_endpoint.discover_router_endpoint(
        run=run,
        include_tailscale=False,
    )

    assert result.listen_host == "0.0.0.0"
    assert result.local_url == "http://127.0.0.1:8000"
    assert result.tailscale_status == "skipped"


def test_missing_docker_falls_back_without_claiming_router_is_running():
    def run(_argv, **_kwargs):
        raise FileNotFoundError

    result = router_endpoint.discover_router_endpoint(
        run=run,
        include_tailscale=False,
    )

    assert (result.listen_host, result.listen_port) == ("127.0.0.1", 8000)
    assert result.source == "default"
    assert result.router_running is None


def test_unexecutable_tailscale_is_reported_unavailable():
    def run(_argv, **_kwargs):
        raise PermissionError

    assert router_endpoint._read_magicdns(
        run=run,
        find_cli=lambda: "/installed/tailscale",
    ) == (None, "unavailable")


def test_explicit_host_and_port_override_docker_binding():
    def run(_argv, **_kwargs):
        return _completed(stdout=_docker_inspect())

    result = router_endpoint.discover_router_endpoint(
        host="100.64.0.10",
        port=9000,
        run=run,
        include_tailscale=False,
    )

    assert result.listen_host == "100.64.0.10"
    assert result.listen_port == 9000
    assert result.source == "override"


def test_find_tailscale_cli_uses_path_on_every_platform():
    assert router_endpoint.find_tailscale_cli(
        platform="linux",
        which=lambda name: "/custom/tailscale" if name == "tailscale" else None,
    ) == "/custom/tailscale"


def test_find_tailscale_cli_finds_windows_install_location():
    expected = Path("C:/Program Files/Tailscale/tailscale.exe")
    found = router_endpoint.find_tailscale_cli(
        platform="win32",
        env={"ProgramFiles": "C:/Program Files"},
        which=lambda _name: None,
        is_file=lambda path: Path(path) == expected,
    )
    assert Path(found) == expected


def test_find_tailscale_cli_finds_macos_app_bundle():
    expected = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    found = router_endpoint.find_tailscale_cli(
        platform="darwin",
        env={},
        which=lambda _name: None,
        is_file=lambda path: Path(path) == expected,
    )
    assert found == str(expected)


def test_magicdns_uses_resolved_tailscale_executable():
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _completed(stdout='{"Self":{"DNSName":"node.example.ts.net."}}')

    name, status = router_endpoint._read_magicdns(
        run=run,
        find_cli=lambda: "/opt/tailscale/bin/tailscale",
    )

    assert calls == [["/opt/tailscale/bin/tailscale", "status", "--json"]]
    assert (name, status) == ("node.example.ts.net", "connected")


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


def test_top_level_cli_dispatch_does_not_forward_endpoint_action(monkeypatch, capsys):
    seen = {}

    def discover(**kwargs):
        seen.update(kwargs)
        return router_endpoint.RouterEndpoint(
            "127.0.0.1",
            8000,
            "http://127.0.0.1:8000",
            "default",
            "custom-router",
            None,
            None,
            "skipped",
        )

    monkeypatch.setattr(router_endpoint, "discover_router_endpoint", discover)

    assert cli.main(["router", "endpoint", "--container", "custom-router", "--no-tailscale"]) == 0
    assert seen["container"] == "custom-router"
    assert seen["include_tailscale"] is False
    assert "127.0.0.1:8000" in capsys.readouterr().out
