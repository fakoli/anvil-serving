"""MCP/controller lifecycle commands preserve the CLI cache-reclaim postcondition."""

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import serves as serves_tools


def _manifest(tmp_path):
    path = tmp_path / "serves.toml"
    path.write_text(
        "[[serve]]\n"
        'name = "heavy"\n'
        'container = "vllm-heavy"\nruntime = "docker"\n'
        "port = 30002\n"
        'model = "primary-local"\n'
        'engine = "vllm"\n'
        'up = "docker compose -f compose.yml up -d heavy"\n',
        encoding="utf-8",
    )
    return str(path)


def test_serves_manage_down_removes_by_default_and_can_keep_container(tmp_path):
    manifest = _manifest(tmp_path)

    default = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["heavy"],
    })
    assert default["ok"] is True
    assert [step["kind"] for step in default["data"]["plan"]["commands"]] == [
        "docker_stop",
        "docker_rm_after_stop",
    ]
    assert "--keep-container" not in default["data"]["command"]

    retained = mcp.call_tool("serves_manage", {
        "action": "down",
        "manifest": manifest,
        "names": ["heavy"],
        "keep_container": True,
    })
    assert retained["ok"] is True
    assert [step["kind"] for step in retained["data"]["plan"]["commands"]] == [
        "docker_stop",
    ]
    assert "--keep-container" in retained["data"]["command"]


def test_serves_promote_captures_same_postcondition_without_new_tool(
        tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)

    monkeypatch.setattr(
        serves_tools,
        "_run_argv",
        lambda argv, **_kwargs: {
            "command": argv,
            "returncode": 0,
            "stdout": (
                "promotion complete\n"
                "cache reclaim after serves promote: no-operation-growth "
                "(cache 5.0 GB -> 5.2 GB; distro docker-desktop)\n"
            ),
            "stderr": "",
        },
    )
    result = mcp.call_tool("serves_promote", {
        "manifest": manifest,
        "plan": "heavy-v2",
        "confirm": True,
        "dry_run": False,
        "human_approved": True,
    })
    assert result["ok"] is True
    assert "cache reclaim after serves promote: no-operation-growth" in result["data"]["stdout"]
    names = {tool["name"] for tool in mcp.list_tools()}
    assert "cache_reclaim" not in names


def test_serves_promote_forwards_transaction_bound_prepared_recovery(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    calls = []
    monkeypatch.setattr(serves_tools, "_run_argv", lambda argv, **_kwargs: calls.append(argv) or {
        "command": argv, "returncode": 0, "stdout": "", "stderr": "",
    })
    transaction_id = "a" * 32
    result = mcp.call_tool("serves_promote", {
        "manifest": manifest, "plan": "heavy-v2", "rollback": True,
        "recover_prepared": transaction_id, "confirm": True,
        "dry_run": False, "human_approved": True,
    })
    assert result["ok"] is True
    assert calls[0][-3:] == ["--recover-prepared", transaction_id, "--confirm"]
    schema = next(tool for tool in mcp.list_tools() if tool["name"] == "serves_promote")["inputSchema"]
    assert schema["properties"]["recover_prepared"].items() >= {
        "type": "string", "pattern": "^[0-9a-f]{32}$",
    }.items()


def test_serves_promote_refuses_preview_of_prepared_recovery(tmp_path):
    result = mcp.call_tool("serves_promote", {
        "manifest": _manifest(tmp_path), "plan": "heavy-v2", "rollback": True,
        "recover_prepared": "a" * 32,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == "bad_recovery"
