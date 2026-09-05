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
    )
    argv = ["serve"]
    for flag, value in zip((
        "--workload-benchmark-db", "--workload-media-db", "--workload-recipe-registry",
        "--workload-manifest", "--workload-topology", "--workload-router-resource",
        "--workload-router-auth-env",
    ), values, strict=True):
        argv.extend((flag, value))

    assert controller_cli.main(argv) == 0
    assert tuple(seen[key] for key in _WORKLOAD_KEYS) == values


@pytest.mark.parametrize(
    "argv",
    [
        ["serve", "--workload-benchmark-db"],
        ["serve", "--workload-router-auth-env"],
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
    ):
        assert flag in output
    assert "environment variable name" in output
