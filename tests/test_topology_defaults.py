from pathlib import Path

import pytest

from anvil_serving import cli, init, mcp, topology as topology_module, topology_cli
from anvil_serving.paths import default_topology_path, resolve_topology_path
from anvil_serving.topology import load_topology
from anvil_serving.voice import cli as voice_cli


def _write_minimal_topology(path: Path, topology_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'schema_version = 1\nid = "%s"\n' % topology_id,
        encoding="utf-8",
    )


def test_default_topology_path_uses_operator_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))

    assert default_topology_path() == str(tmp_path / "operator-topology.toml")
    assert resolve_topology_path(None) == str(tmp_path / "operator-topology.toml")


def test_resolve_topology_path_precedence_is_explicit_then_environment_then_home(
    tmp_path, monkeypatch
):
    config_home = tmp_path / "config-home"
    environment_path = tmp_path / "environment.toml"
    explicit_path = tmp_path / "explicit.toml"
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.setenv("ANVIL_VOICE_TOPOLOGY", str(environment_path))

    assert resolve_topology_path(
        str(explicit_path), env_var="ANVIL_VOICE_TOPOLOGY"
    ) == str(explicit_path)
    assert resolve_topology_path(
        None, env_var="ANVIL_VOICE_TOPOLOGY"
    ) == str(environment_path)
    monkeypatch.setenv("ANVIL_VOICE_TOPOLOGY", "  ")
    assert resolve_topology_path(
        None, env_var="ANVIL_VOICE_TOPOLOGY"
    ) == str(config_home / "operator-topology.toml")


def test_topology_show_uses_config_home_when_flag_is_omitted(tmp_path, monkeypatch):
    source = tmp_path / "operator-topology.toml"
    _write_minimal_topology(source, "home-default")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))

    result = topology_cli.run(["show"])

    assert result["topology"] == "home-default"
    assert result["topology_path"] == str(source)


def test_topology_show_explicit_path_wins_over_config_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    _write_minimal_topology(home / "operator-topology.toml", "home-default")
    _write_minimal_topology(explicit, "explicit")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))

    result = topology_cli.run(["show", "--topology", str(explicit)])

    assert result["topology"] == "explicit"
    assert result["topology_path"] == str(explicit)


def test_target_resolution_options_default_the_topology_without_enabling_bare_commands(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    requested = cli._ResolutionOptions(target="host:dark")
    bare = cli._ResolutionOptions()

    effective = cli._effective_resolution_options(requested)

    assert effective.topology == str(tmp_path / "operator-topology.toml")
    assert cli._effective_resolution_options(bare) is bare
    assert cli._resolution_options_argv(effective)[:2] == (
        "--topology",
        str(tmp_path / "operator-topology.toml"),
    )


def test_voice_topology_options_default_to_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    parser = voice_cli.build_parser()

    audio = parser.parse_args(["audio", "status"])
    proxy = parser.parse_args(["proxy", "bridge", "--dry-run"])

    assert audio.topology == str(tmp_path / "operator-topology.toml")
    assert proxy.topology == str(tmp_path / "operator-topology.toml")


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


def test_init_personalizes_default_topology_for_detected_gpu_host(tmp_path):
    primary = "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    auxiliary = "GPU-11111111-2222-3333-4444-555555555555"
    tailnet_ip = "100.100.100.100"
    gpu_output = (
        "0, %s, Primary Card, 97887\n"
        "1, %s, Auxiliary Card, 32607\n"
    ) % (primary, auxiliary)

    init.scaffold_home(
        out_dir=str(tmp_path),
        _gpu_run=lambda *args, **kwargs: gpu_output,
        _tailscale_run=lambda *args, **kwargs: tailnet_ip + "\n",
    )

    topology_path = tmp_path / "operator-topology.toml"
    topology_text = topology_path.read_text(encoding="utf-8")
    topology = load_topology(topology_path)

    assert topology.command_host == "fakoli-dark"
    assert topology.command_runtime == "dark-native"
    assert topology.host("fakoli-dark").address == tailnet_ip
    assert {role.uuid for role in topology.gpu_roles} == {primary, auxiliary}
    assert "192.0.2.20" not in topology_text
    assert "GPU-00000000-0000-0000-0000-00000000000" not in topology_text
