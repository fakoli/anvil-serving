from pathlib import Path

from anvil_serving import mcp


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
    assert data["preview"]["model_ids"] == ["llm.primary", "llm.voice"]
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
