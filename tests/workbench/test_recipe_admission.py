"""Manifest ownership and capacity gate for portal recipe loads."""
from types import SimpleNamespace

import pytest

from anvil_serving import reservations
from anvil_serving.topology import GpuRole
from anvil_serving.workbench_app import recipe_admission as admission


@pytest.fixture
def owner(monkeypatch, tmp_path):
    args = {"manifest": str(tmp_path / "serves.toml"), "serve": "candidate", "topology": str(tmp_path / "topology.toml"), "registry": str(tmp_path / "recipes.toml"), "container": "candidate", "command_host": "host-a", "command_runtime": "native"}
    recipe = {"model": "org/model", "serve": {"port": 30001}, "hardware": {"gpu_uuid": "GPU-A"}}
    budget = reservations.GpuRoleBudget("compute", 10000, 1000)
    target = {"name": "candidate", "model": "org/model", "runtime": "docker", "container": "candidate", "port": 30001,
              "gpu_role": "compute", "vram_mib": 9000,
              reservations.GPU_ROLES_KEY: {"compute": budget},
              "up": ["/opt/bin/anvil-serving", "models", "recipes", "load", "org/model", "--registry", args["registry"], "--container", "candidate", "--confirm"]}
    scope, states = [target], {"candidate": "absent"}
    monkeypatch.setattr(admission.serves, "load_manifest_set", lambda _: scope)
    monkeypatch.setattr(admission.serves, "docker_states", lambda _: states)
    monkeypatch.setattr(admission, "load_topology", lambda _: SimpleNamespace(gpu_roles=[GpuRole("compute", "host-a", "native", "GPU-A", 10000, 1000)]))
    return args, recipe, target, scope, states


def test_exact_manifest_recipe_admission_pins_owner_state(owner):
    args, recipe, _, _, states = owner
    preview = admission.load_plan(args, recipe, {"containers": []})
    assert preview["reservation"] == "full-card" and preview["gpu_roles"] == ["compute"]
    states["candidate"] = "exited"
    assert admission.load_plan(args, recipe, {"containers": []})["admission_sha256"] != preview["admission_sha256"]
