
import pytest

from anvil_serving import mcp, topology as topology_module


@pytest.mark.parametrize(
    "tool,args",
    [
        (
            mcp.tool_voice_manage,
            {
                "action": "status",
                "config": "examples/voice/voice.example.toml",
            },
        ),
        (
            mcp.tool_voice_proxy_manage,
            {
                "action": "status",
                "config": "examples/voice/voice.example.toml",
            },
        ),
    ],
)
def test_mcp_voice_tools_reach_for_config_home_topology(
    tool, args, tmp_path, monkeypatch
):
    class TopologyReadReached(RuntimeError):
        pass

    captured = {}

    def stop_at_topology(path, *unused):
        captured["path"] = path
        raise TopologyReadReached

    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.delenv("ANVIL_VOICE_TOPOLOGY", raising=False)
    monkeypatch.setattr(topology_module, "load_topology", stop_at_topology)

    with pytest.raises(TopologyReadReached):
        tool(args)

    assert captured["path"] == str(tmp_path / "operator-topology.toml")
