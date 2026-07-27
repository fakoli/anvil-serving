from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import router as router_tools


def test_tools_expose_direct_serving_operations_without_legacy_router_controls():
    assert {"preflight_probe", "benchmark_probe", "serves_manage", "voice_manage"}.issubset(mcp.TOOLS)
    assert "route_decision" not in mcp.TOOLS
    assert "router_promote" not in mcp.TOOLS
    assert not hasattr(mcp, "tool_route_decision")
    assert not hasattr(mcp, "tool_router_promote")


def test_openclaw_sync_schema_is_direct_alias_only():
    properties = mcp.TOOLS["openclaw_sync"]["inputSchema"]["properties"]
    assert "plugin_dir" not in properties
    assert "route_endpoint" not in properties
    assert "native_provider" not in properties


def test_openclaw_sync_apply_writes_direct_alias_provider(tmp_path):
    config = Path(__file__).resolve().parents[1] / "configs" / "example.toml"
    out = tmp_path / "openclaw.json"

    result = mcp.tool_openclaw_sync({
        "config": str(config),
        "out": str(out),
        "dry_run": False,
        "confirm": True,
    })

    assert result["ok"]
    data = result["data"]
    assert data["applied"] is True
    assert data["preview"]["direct_aliases"] is True
    assert data["preview"]["model_ids"] == [
        "llm.primary",
        "llm.voice",
        "vision.ocr",
        "vision.general",
    ]
    assert "plugin_id" not in data["preview"]
    assert "route_endpoint" not in data["target"]
    assert out.is_file()


def test_workflow_promotion_requires_a_serves_promotion_result():
    packet = {
        "schema_version": "operator-workflow/v1", "request": "benchmark", "gate_state": "human_required",
        "targets": {}, "tools_used": [], "artifacts": [], "advisory_priors": [],
        "recommendation": "promote", "human_gate_required": True, "promoted": False,
    }
    assert mcp.validate_workflow_packet(packet)["valid"]
    packet["promoted"] = True
    assert not mcp.validate_workflow_packet(packet)["valid"]


def test_router_manage_confirmed_call_forwards_lifecycle_options(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.yml"
    env_file = tmp_path / "router.env"
    calls = []
    monkeypatch.setattr(
        router_tools,
        "_run_argv",
        lambda argv, *, confirm, timeout: calls.append((argv, confirm, timeout)) or {"returncode": 0},
    )

    result = mcp.tool_router_manage({
        "action": "up",
        "compose": str(compose),
        "service": "router",
        "env_file": str(env_file),
        "recreate": True,
        "dry_run": False,
        "confirm": True,
    })

    assert result["ok"]
    assert result["data"]["applied"] is True
    argv, confirm, timeout = calls[0]
    assert confirm is True
    assert timeout == 300
    assert argv[-2:] == ["--recreate", "--confirm"]
    assert ["--compose", str(compose)] == argv[argv.index("--compose"):argv.index("--compose") + 2]
    assert ["--service", "router"] == argv[argv.index("--service"):argv.index("--service") + 2]
    assert ["--env-file", str(env_file)] == argv[argv.index("--env-file"):argv.index("--env-file") + 2]
    assert result["data"]["lifecycle_command"] == [
        "docker", "compose", "--env-file", str(env_file), "-f", str(compose), "up", "-d",
        "--no-deps", "--force-recreate", "router",
    ]


def test_router_manage_preview_reports_target_and_does_not_invoke_docker(tmp_path, monkeypatch):
    compose = tmp_path / "docker-compose.yml"
    env_file = tmp_path / "router.env"
    monkeypatch.setattr(
        router_tools,
        "_run_argv",
        lambda *_args, **_kwargs: pytest.fail("preview invoked Docker"),
    )

    result = mcp.tool_router_manage({
        "action": "up",
        "compose": str(compose),
        "service": "router",
        "env_file": str(env_file),
    })

    assert result["ok"]
    data = result["data"]
    assert data["applied"] is False
    assert data["dry_run"] is True
    assert data["target"] == {
        "action": "up",
        "compose": str(compose),
        "container": "anvil-router",
        "env_file": str(env_file),
        "no_verify": False,
        "recreate": False,
        "service": "router",
        "timeout_seconds": 300,
    }
    assert "--force-recreate" not in data["lifecycle_command"]
    assert "--dry-run" in data["command"]
