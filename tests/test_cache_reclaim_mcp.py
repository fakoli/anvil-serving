"""MCP/controller lifecycle commands preserve the CLI cache-reclaim postcondition."""
import sys
import types

from anvil_serving import mcp


def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def _manifest(tmp_path):
    path = tmp_path / "serves.toml"
    path.write_text(
        "[[serve]]\n"
        'name = "heavy"\n'
        'container = "vllm-heavy"\n'
        "port = 30002\n"
        'model = "heavy-local"\n'
        'engine = "vllm"\n'
        'up = "docker compose -f compose.yml up -d heavy"\n',
        encoding="utf-8",
    )
    return str(path)


def test_serves_manage_captures_cli_cache_reclaim_postcondition(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return _proc(
            0,
            "compose up heavy\n"
            "cache reclaim after serves up: reclaimed "
            "(cache 24.0 GB -> 3.0 GB; distro docker-desktop)\n",
        )

    monkeypatch.setattr(mcp.subprocess, "run", run)
    result = mcp.call_tool("serves_manage", {
        "action": "up",
        "manifest": manifest,
        "names": ["heavy"],
        "confirm": True,
        "dry_run": False,
    })
    assert result["ok"] is True
    assert result["data"]["applied"] is True
    assert "cache reclaim after serves up: reclaimed" in result["data"]["stdout"]
    assert seen[0][0][:5] == [
        sys.executable, "-m", "anvil_serving.cli", "serves", "up",
    ]
    assert "--confirm" in seen[0][0]


def test_serves_promote_captures_same_postcondition_without_new_tool(
        tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)

    monkeypatch.setattr(
        mcp.subprocess,
        "run",
        lambda *_args, **_kwargs: _proc(
            0,
            "promotion complete\n"
            "cache reclaim after serves promote: no-operation-growth "
            "(cache 5.0 GB -> 5.2 GB; distro docker-desktop)\n",
        ),
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
