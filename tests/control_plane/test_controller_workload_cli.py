from anvil_serving.control_plane.controller import cli as controller_cli


def test_fleet_topology_does_not_enable_node_router_source(monkeypatch):
    calls = []; monkeypatch.setattr(controller_cli, "serve", lambda **kwargs: calls.append(kwargs) or 0); path = "relative fleet topology.toml"
    assert controller_cli.main(["serve", "--workload-fleet-topology", path]) == 0
    assert len(calls) == 1 and calls[0]["workload_fleet_topology"] == path
    assert all(calls[0][key] is None for key in ("workload_benchmark_db", "workload_media_db", "workload_recipe_registry", "workload_manifest", "workload_topology", "workload_router_resource", "workload_router_auth_env"))
