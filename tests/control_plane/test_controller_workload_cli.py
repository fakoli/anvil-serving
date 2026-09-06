import pytest

from anvil_serving.control_plane.controller import cli as controller_cli


_WORKLOAD_KEYS = (
    "workload_benchmark_db",
    "workload_media_db",
    "workload_recipe_registry",
    "workload_manifest",
    "workload_topology",
    "workload_router_resource",
    "workload_router_auth_env",
    "workload_fleet_topology",
)


def test_controller_serve_defaults_all_workload_sources_to_none(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: seen.update(kwargs) or 0)

    assert controller_cli.main(["serve", "--node-id", "node-a", "--port", "9000"]) == 0
    assert {key: seen[key] for key in _WORKLOAD_KEYS} == {key: None for key in _WORKLOAD_KEYS}
    assert seen["node_id"] == "node-a"
    assert seen["port"] == 9000


def test_controller_serve_forwards_explicit_workload_source_options_unchanged(monkeypatch):
    seen = {}
    monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: seen.update(kwargs) or 0)
    values = (
        "benchmark.sqlite", "media.sqlite", "recipes.toml", "serves.toml",
        "topology.toml", "router-observation", "ROUTER_WORKLOAD_TOKEN",
        "fleet-topology.toml",
    )
    argv = ["serve"]
    for flag, value in zip((
        "--workload-benchmark-db", "--workload-media-db", "--workload-recipe-registry",
        "--workload-manifest", "--workload-topology", "--workload-router-resource",
        "--workload-router-auth-env",
        "--workload-fleet-topology",
    ), values, strict=True):
        argv.extend((flag, value))

    assert controller_cli.main(argv) == 0
    assert tuple(seen[key] for key in _WORKLOAD_KEYS) == values


@pytest.mark.parametrize(
    "argv",
    [
        ["serve", "--workload-benchmark-db"],
        ["serve", "--workload-router-auth-env"],
        ["serve", "--workload-fleet-topology"],
        ["serve", "--workload-endpoint", "private"],
    ],
)
def test_workload_option_parse_failures_do_not_invoke_serve(monkeypatch, argv):
    monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: pytest.fail("serve must not run"))
    with pytest.raises(SystemExit) as exc:
        controller_cli.main(argv)
    assert exc.value.code == 2


def test_serve_help_describes_explicit_paths_and_router_environment(capsys):
    with pytest.raises(SystemExit) as exc:
        controller_cli.main(["serve", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--workload-benchmark-db", "--workload-media-db", "--workload-recipe-registry",
        "--workload-manifest", "--workload-topology", "--workload-router-resource",
        "--workload-router-auth-env",
        "--workload-fleet-topology",
    ):
        assert flag in output
    assert "environment variable name" in output
    assert "declared fleet workload aggregation" in " ".join(output.split())


def test_fleet_topology_does_not_enable_node_router_source(monkeypatch):
    calls = []
    monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: calls.append(kwargs) or 0)
    path = "relative fleet topology.toml"
    assert controller_cli.main(["serve", "--workload-fleet-topology", path]) == 0
    assert len(calls) == 1
    assert calls[0]["workload_fleet_topology"] == path
    assert all(calls[0][key] is None for key in _WORKLOAD_KEYS if key != "workload_fleet_topology")


def test_top_level_help_exposes_fleet_topology_without_running_serve(monkeypatch, capsys):
    from anvil_serving import cli

    monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: pytest.fail("serve must not run"))
    assert cli.main(["controller", "serve", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--workload-fleet-topology" in output
    assert "declared fleet workload aggregation" in " ".join(output.split())
