"""Typed MCP facade coverage for live router route transactions."""

import pytest

from anvil_serving.control_plane.mcp.tools import router
from anvil_serving.control_plane.mcp.errors import ToolError


def test_router_routes_preview_uses_conventional_endpoint_and_declared_auth(monkeypatch):
    captured = {}
    monkeypatch.setenv("ROUTER_ROUTE_TOKEN", "test-token")

    def request(action, **kwargs):
        captured.update(action=action, **kwargs)
        return {"preview_id": "a" * 32}

    monkeypatch.setattr("anvil_serving.router_manage.route_update_request", request)
    result = router.tool_router_routes({
        "action": "preview", "config": "[router]\n", "auth_env": "ROUTER_ROUTE_TOKEN",
    })
    assert result["data"] == {"preview_id": "a" * 32}
    assert captured["action"] == "preview"
    assert captured["candidate_bytes"] == b"[router]\n"
    assert captured["env"]["ANVIL_ROUTER_URL"] == "http://127.0.0.1:8000"
    assert captured["env"]["ANVIL_ROUTER_TOKEN"] == "test-token"


def test_router_routes_apply_requires_typed_confirmation_and_never_has_dry_run(monkeypatch):
    monkeypatch.setenv("ANVIL_ROUTER_TOKEN", "test-token")
    with pytest.raises(ToolError) as exc:
        router.tool_router_routes({"action": "apply", "preview_id": "a" * 32, "confirm": "true"})
    assert exc.value.code == "bad_argument"
    schema = router.FAMILY.tools["router_routes"]["inputSchema"]
    assert "dry_run" not in schema["properties"]
    assert schema["properties"]["confirm"] == {"type": "boolean"}


def test_router_routes_refuses_missing_declared_credential(monkeypatch):
    monkeypatch.delenv("ROUTER_ROUTE_TOKEN", raising=False)
    with pytest.raises(ToolError) as exc:
        router.tool_router_routes({"action": "status", "auth_env": "ROUTER_ROUTE_TOKEN"})
    assert exc.value.code == "route_update_refused"
