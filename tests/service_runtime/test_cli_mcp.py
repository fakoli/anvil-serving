"""Public CLI and MCP parity for declared host services."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


def _cli_module():
    return importlib.import_module("anvil_serving.service_runtime.cli")


def _service_tools_module():
    return importlib.import_module("anvil_serving.control_plane.mcp.tools.services")


def test_cli_adopt_forwards_a_launchd_binding_to_the_shared_executor(monkeypatch, capsys, tmp_path):
    cli = _cli_module()
    monkeypatch.setattr(os, "getuid", lambda: 501, raising=False)
    seen = {}

    def execute(action, service=None, **kwargs):
        seen.update(action=action, service=service, **kwargs)
        return {"action": action, "service": service, "applied": False}

    monkeypatch.setattr(cli, "execute", execute)
    manifest = tmp_path / "services.toml"

    assert cli.main(
        [
            "adopt",
            "voice-stt",
            "--manifest",
            str(manifest),
            "--manager",
            "launchd",
            "--service-label",
            "io.anvil.voice.stt",
            "--resource",
            "stt-serve",
            "--engine",
            "mlx-lm",
            "--support",
            "legacy",
        ]
    ) == 0

    assert seen == {
        "action": "adopt",
        "service": "voice-stt",
        "manifest": str(manifest),
        "topology": None,
        "topology_overlay": None,
        "command_host": None,
        "command_runtime": None,
        "target": None,
        "transport": "local",
        "dry_run": True,
        "confirm": False,
        "tail": 100,
        "timeout_seconds": 30,
        "remote": False,
        "binding": {
            "id": "voice-stt",
            "resource": "stt-serve",
            "manager": "launchd",
            "engine": "mlx-lm",
            "support": "legacy",
            "label": "io.anvil.voice.stt",
            "owner_uid": os.getuid(),
            "definition": str(
                Path.home() / "Library" / "LaunchAgents" / "io.anvil.voice.stt.plist"
            ),
        },
    }
    assert json.loads(capsys.readouterr().out) == {
        "action": "adopt",
        "service": "voice-stt",
        "applied": False,
    }


def test_cli_reports_structured_service_errors_without_a_traceback(monkeypatch, capsys):
    cli = _cli_module()
    contracts = importlib.import_module("anvil_serving.service_runtime.contracts")

    def execute(*_args, **_kwargs):
        raise contracts.ServiceError("service_not_found", "declared service was not found")

    monkeypatch.setattr(cli, "execute", execute)

    assert cli.main(["status", "missing-service"]) == 2
    assert json.loads(capsys.readouterr().err) == {
        "code": "service_not_found",
        "message": "declared service was not found",
    }


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


def test_cli_adoption_rejects_disabled_startup_policy(monkeypatch):
    cli = _cli_module()
    monkeypatch.setattr(cli, "execute", lambda *_args, **_kwargs: {})

    with pytest.raises(SystemExit) as raised:
        cli.main(
            [
                "adopt", "voice-stt", "--manager", "docker", "--container", "voice-stt",
                "--resource", "voice", "--engine", "kokoro", "--startup-policy", "no",
            ]
        )

    assert raised.value.code == 2


def test_mcp_status_uses_owner_manifest_and_the_shared_remote_executor(monkeypatch):
    services = _service_tools_module()
    seen = {}

    def execute(action, service=None, **kwargs):
        seen.update(action=action, service=service, **kwargs)
        return {"service": service, "registered": True}

    monkeypatch.setattr(services, "execute", execute)

    assert services.tool_host_services_status({"service": "voice-stt"}) == {
        "ok": True,
        "data": {"service": "voice-stt", "registered": True},
    }
    assert seen == {
        "action": "status",
        "service": "voice-stt",
        "manifest": None,
        "topology": None,
        "topology_overlay": None,
        "command_host": None,
        "command_runtime": None,
        "target": None,
        "transport": "local",
        "dry_run": True,
        "confirm": False,
        "tail": 100,
        "timeout_seconds": 30,
        "binding": None,
        "remote": True,
    }


def test_mcp_manage_rejects_raw_process_arguments_before_execution(monkeypatch):
    services = _service_tools_module()
    called = False

    def execute(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(services, "execute", execute)
    errors = importlib.import_module("anvil_serving.control_plane.mcp.errors")

    with pytest.raises(errors.ToolError) as raised:
        services.tool_host_services_manage(
            {"action": "up", "service": "voice-stt", "argv": ["/bin/sh", "-c", "bad"]}
        )

    assert raised.value.code == "bad_argument"
    assert called is False


def test_mcp_adoption_rejects_disabled_startup_policy_before_execution(monkeypatch):
    services = _service_tools_module()
    called = False

    def execute(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(services, "execute", execute)
    errors = importlib.import_module("anvil_serving.control_plane.mcp.errors")

    with pytest.raises(errors.ToolError) as raised:
        services.tool_host_services_manage(
            {
                "action": "adopt", "service": "voice-stt", "manager": "docker",
                "container": "voice-stt", "resource": "voice", "engine": "kokoro",
                "startup_policy": "no",
            }
        )

    assert raised.value.code == "bad_argument"
    assert called is False


def test_public_cli_reaches_real_service_executor(tmp_path, capsys, monkeypatch):
    from anvil_serving.cli import main
    topology = tmp_path / "topology.toml"
    topology.write_text('''schema_version = 1
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
''')
    monkeypatch.delenv("ANVIL_COMMAND_HOST", raising=False)
    monkeypatch.delenv("ANVIL_COMMAND_RUNTIME", raising=False)
    assert main(["--json", "host", "services", "capabilities", "--topology", str(topology)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["data"]["schema"] == "anvil-services-capabilities/v1"


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
