"""Public CLI and MCP parity for declared host services."""

from __future__ import annotations

import importlib
import json

import pytest


def _cli_module():
    return importlib.import_module("anvil_serving.service_runtime.cli")


def _service_tools_module():
    return importlib.import_module("anvil_serving.control_plane.mcp.tools.services")


def test_windows_launchd_adoption_returns_structured_errors(monkeypatch, capsys):
    from types import SimpleNamespace
    cli = _cli_module()
    services = _service_tools_module()
    errors = importlib.import_module("anvil_serving.control_plane.mcp.errors")
    monkeypatch.setattr(cli, "os", SimpleNamespace())
    monkeypatch.setattr(services, "os", SimpleNamespace())

    assert cli.main([
        "adopt", "voice-stt", "--manager", "launchd", "--resource", "voice",
        "--engine", "mlx-lm", "--service-label", "org.example.voice-stt",
    ]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "unsupported_platform"
    with pytest.raises(errors.ToolError) as raised:
        services.tool_host_services_manage({
            "action": "adopt", "service": "voice-stt", "manager": "launchd",
            "resource": "voice", "engine": "mlx-lm", "service_label": "org.example.voice-stt",
        })
    assert raised.value.code == "unsupported_platform"


def test_cli_and_mcp_forward_external_compose_identity(monkeypatch):
    cli = _cli_module()
    services = _service_tools_module()
    seen = []

    def execute(*_args, **kwargs):
        seen.append(kwargs["binding"])
        return {"applied": False}

    monkeypatch.setattr(cli, "execute", execute)
    assert cli.main([
        "adopt", "metrics", "--manager", "docker", "--resource", "metrics", "--engine", "external",
        "--container", "metrics", "--external-compose", "--compose-project", "obs",
        "--compose-service", "prometheus", "--compose-config-source", "/ops/prometheus",
        "--compose-config-target", "/etc/prometheus", "--expected-image-id", "sha256:" + "a" * 64,
    ]) == 0
    monkeypatch.setattr(services, "execute", execute)
    services.tool_host_services_manage({
        "action": "adopt", "service": "metrics", "manager": "docker", "resource": "metrics",
        "engine": "external", "container": "metrics", "external_compose": True,
        "compose_project": "obs", "compose_service": "prometheus",
        "compose_config_source": "/ops/prometheus", "compose_config_target": "/etc/prometheus",
        "expected_image_id": "sha256:" + "a" * 64,
    })
    assert seen[0] == seen[1]


def test_public_cli_service_mutation_previews_until_no_dry_run_and_confirm(
    monkeypatch, tmp_path, capsys
):
    """The public command retains mutation metadata but previews by default."""
    from anvil_serving.cli import main

    runtime_cli = _cli_module()
    calls = []

    def execute(action, service=None, **kwargs):
        calls.append((action, service, kwargs))
        return {"action": action, "service": service, "applied": not kwargs["dry_run"]}

    monkeypatch.setattr(runtime_cli, "execute", execute)
    topology = tmp_path / "topology.toml"
    topology.write_text(
        '''schema_version = 1
id = "local"
command_host = "host:mac"
command_runtime = "runtime:native"
[[hosts]]
id = "mac"
os = "macos"
roles = ["operator"]
[[runtimes]]
id = "native"
host = "mac"
role = "native"
[[resources]]
id = "host"
role = "host"
host = "mac"
runtime = "native"
workload = "service"
''',
        encoding="utf-8",
    )
    monkeypatch.delenv("ANVIL_COMMAND_HOST", raising=False)
    monkeypatch.delenv("ANVIL_COMMAND_RUNTIME", raising=False)
    base = [
        "--json", "host", "services", "up", "events", "--topology", str(topology),
    ]

    assert main(base) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["ok"] is True
    assert preview["data"]["applied"] is False
    assert len(calls) == 1
    assert calls[0][0:2] == ("up", "events")
    assert calls[0][2]["dry_run"] is True
    assert calls[0][2]["confirm"] is False

    assert main([*base, "--no-dry-run"]) == 3
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["error"]["code"] == "confirmation_required"
    assert len(calls) == 1

    assert main([*base, "--no-dry-run", "--confirm"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["ok"] is True
    assert applied["data"]["applied"] is True
    assert calls[1][0:2] == ("up", "events")
    assert calls[1][2]["dry_run"] is False
    assert calls[1][2]["confirm"] is True
