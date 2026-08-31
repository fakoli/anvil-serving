from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from anvil_serving import cli, models, reservations, serve_recipes, serves
from anvil_serving.control_plane.mcp.tools import models as models_tools


def _completed(argv, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _container_id(container: str) -> str:
    return (container.encode("utf-8").hex() * 64)[:64]


def _row(
    container: str,
    *,
    container_id: str | None = None,
    model: str = "org/model",
    state: str = "running",
    running: bool = True,
    labels: dict | None = None,
) -> dict:
    resolved_labels = {
        serve_recipes.RECIPE_MANAGED_LABEL: serve_recipes.RECIPE_MANAGED_VALUE,
        serve_recipes.RECIPE_MODEL_LABEL: model,
        serve_recipes.RECIPE_REVISION_LABEL: "a" * 40,
        serve_recipes.RECIPE_DIGEST_LABEL: "b" * 64,
        serve_recipes.RECIPE_REGISTRY_DIGEST_LABEL: "c" * 64,
        serve_recipes.RECIPE_NATIVE_KV_OFFLOAD_LABEL: "false",
    }
    if labels:
        resolved_labels.update(labels)
    return {
        "Id": container_id or _container_id(container),
        "Name": "/" + container,
        "Image": "sha256:" + "d" * 64,
        "Args": [
            "/weights/model",
            "--served-model-name",
            "served-model",
            "--api-key",
            "must-not-leak",
        ],
        "Config": {
            "Labels": resolved_labels,
            "Env": ["HF_TOKEN=must-not-leak"],
            "Image": "registry.invalid/private:tag",
        },
        "HostConfig": {
            "PortBindings": {
                "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39077"}],
            },
            "DeviceRequests": [
                {"DeviceIDs": ["GPU-a", "GPU-b"], "Count": 0},
            ],
        },
        "State": {
            "Status": state,
            "Running": running,
            "Health": {"Status": "healthy" if running else "unhealthy"},
        },
    }


class _DiscoveryRun:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[1] == "ps":
            ids = [format(index + 1, "012x") for index in range(len(self.rows))]
            return _completed(argv, stdout="\n".join(ids) + ("\n" if ids else ""))
        assert argv[1] == "inspect"
        return _completed(argv, stdout=json.dumps(self.rows))


def test_recipe_container_discovery_with_no_containers_is_bounded() -> None:
    run = _DiscoveryRun([])

    inventory = serve_recipes.discover_recipe_containers(_run=run)

    assert inventory == {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [],
    }
    assert len(run.calls) == 1
    assert run.calls[0][1:3] == ["ps", "-a"]


def test_recipe_container_discovery_returns_only_safe_typed_fields() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("candidate")])
    )

    assert inventory["containers"] == [
        {
            "container": "candidate",
            "container_id": _container_id("candidate"),
            "model": "org/model",
            "revision": "a" * 40,
            "recipe_digest": "b" * 64,
            "registry_digest": "c" * 64,
            "image_digest": "sha256:" + "d" * 64,
            "served_identity": "served-model",
            "bound_port": 39077,
            "bound_ports": [39077],
            "gpu_selection": ["GPU-a", "GPU-b"],
            "state": "running",
            "running": True,
            "health": "healthy",
            "native_kv_offload": False,
        }
    ]
    encoded = json.dumps(inventory)
    assert "must-not-leak" not in encoded
    assert "HF_TOKEN" not in encoded
    assert "registry.invalid" not in encoded
    assert "127.0.0.1" not in encoded


def test_recipe_container_discovery_includes_exited_owned_container() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("stopped", state="exited", running=False)])
    )

    assert inventory["containers"][0]["state"] == "exited"
    assert inventory["containers"][0]["running"] is False


def test_recipe_container_discovery_skips_missing_malformed_and_non_anvil_labels() -> None:
    missing_id = _row("missing-id")
    del missing_id["Id"]
    missing_model = _row("missing")
    del missing_model["Config"]["Labels"][serve_recipes.RECIPE_MODEL_LABEL]
    malformed_digest = _row(
        "malformed",
        labels={serve_recipes.RECIPE_DIGEST_LABEL: "not-a-digest"},
    )
    non_anvil = _row(
        "foreign",
        labels={serve_recipes.RECIPE_MANAGED_LABEL: "someone-else"},
    )

    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([missing_id, missing_model, malformed_digest, non_anvil])
    )

    assert inventory["containers"] == []


def test_recipe_container_selection_refuses_two_containers_for_same_model() -> None:
    inventory = serve_recipes.discover_recipe_containers(
        _run=_DiscoveryRun([_row("one"), _row("two")])
    )

    with pytest.raises(serve_recipes.RecipeError, match="ambiguous"):
        serve_recipes.select_recipe_container(inventory, model="org/model")

    selected = serve_recipes.select_recipe_container(
        inventory,
        model="org/model",
        container="two",
    )
    assert selected["container"] == "two"


def test_recipe_load_labels_canonical_recipe_and_registry_digests() -> None:
    recipe = {
        "model": "org/model",
        "serve": {"image": "image@sha256:" + "a" * 64},
    }
    argv = serve_recipes.docker_run_argv(
        recipe,
        container="candidate",
        registry_digest_value="f" * 64,
    )
    labels = [argv[index + 1] for index, token in enumerate(argv[:-1]) if token == "--label"]

    assert "%s=%s" % (
        serve_recipes.RECIPE_DIGEST_LABEL,
        serve_recipes.recipe_digest(recipe),
    ) in labels
    assert "%s=%s" % (
        serve_recipes.RECIPE_REGISTRY_DIGEST_LABEL,
        "f" * 64,
    ) in labels
    assert "%s=false" % serve_recipes.RECIPE_NATIVE_KV_OFFLOAD_LABEL in labels


def test_models_recipes_running_json_uses_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [serve_recipes._recipe_container_record(_row("candidate"))],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert models._recipe_main(["running", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == inventory


def test_canonical_recipe_running_json_envelope_keeps_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [serve_recipes._recipe_container_record(_row("candidate"))],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert cli.main(["models", "recipes", "running", "--json"]) == 0

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["data"] == inventory


def test_recipe_status_survives_missing_origin_registry(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [identity],
    }
    missing_registry = tmp_path / "removed-registry.toml"
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    assert models._recipe_main([
        "status",
        "org/model",
        "--registry",
        str(missing_registry),
    ]) == 0

    assert json.loads(capsys.readouterr().out) == identity


def test_discovered_recipe_unload_removes_revalidated_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [identity],
        },
    )
    calls = []

    assert models._discovered_recipe_container_unload(
        identity,
        confirm=True,
        _run=lambda argv, **_kwargs: calls.append(argv) or _completed(argv),
    ) == 0

    assert calls == [["docker", "rm", "-f", identity["container_id"]]]
    assert "candidate" in capsys.readouterr().out


def test_discovered_recipe_logs_read_revalidated_immutable_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = serve_recipes._recipe_container_record(_row("candidate"))
    assert identity is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [identity],
        },
    )
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return _completed(argv, stdout="candidate ready\n")

    assert models._discovered_recipe_container_logs(
        identity,
        tail=17,
        _run=run,
    ) == 0

    assert calls == [
        ["docker", "logs", "--tail", "17", identity["container_id"]]
    ]
    assert capsys.readouterr().out == "candidate ready\n"


def test_discovered_recipe_unload_refuses_same_name_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = serve_recipes._recipe_container_record(
        _row("candidate", container_id="a" * 64)
    )
    replacement = serve_recipes._recipe_container_record(
        _row("candidate", container_id="f" * 64)
    )
    assert selected is not None and replacement is not None
    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [replacement],
        },
    )
    calls = []

    with pytest.raises(serve_recipes.RecipeError, match="identity changed"):
        models._discovered_recipe_container_unload(
            selected,
            confirm=True,
            _run=lambda argv, **_kwargs: calls.append(argv) or _completed(argv),
        )

    assert calls == []


def test_recipe_containers_mcp_returns_the_same_typed_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = {
        "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
        "containers": [],
    }
    monkeypatch.setattr(serve_recipes, "discover_recipe_containers", lambda: inventory)

    result = models_tools.tool_recipe_containers({})

    assert result["ok"] is True
    assert result["data"]["inventory"] == inventory


def _mode_serves() -> list[dict]:
    budgets = {
        "gpu-a": reservations.GpuRoleBudget("gpu-a", 96_000),
        "gpu-b": reservations.GpuRoleBudget("gpu-b", 96_000),
    }
    return [
        {
            "name": "split-a",
            "container": "split-a",
            "gpu_role": "gpu-a",
            "groups": ["split"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            reservations.GPU_ROLES_KEY: budgets,
        },
        {
            "name": "split-b",
            "container": "split-b",
            "gpu_role": "gpu-b",
            "groups": ["split"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            reservations.GPU_ROLES_KEY: budgets,
        },
        {
            "name": "tp2",
            "container": "tp2",
            "gpu_roles": ["gpu-a", "gpu-b"],
            "vram_mib": 90_000,
            "gpu_inference": True,
            "operating_mode": serves.DUAL_GPU_EXCLUSIVE_MODE,
            "tensor_parallel_size": 2,
            reservations.GPU_ROLES_KEY: budgets,
        },
    ]


def _recipe_owner() -> dict:
    return {
        "owner": "recipe:candidate",
        "classification": "unmanaged-by-manifest",
        "container": "candidate",
        "model": "org/model",
        "state": "running",
        "gpu_selection": ["GPU-a", "GPU-b"],
        "gpu_roles": ["gpu-a", "gpu-b"],
        "unresolved_gpu_selection": [],
    }


def test_operating_mode_marks_recipe_owned_roles_unresolved_not_free() -> None:
    summary = serves.operating_mode_summary(
        _mode_serves(),
        lambda _container: "absent",
        {
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert summary["mode"] == "unresolved"
    assert summary["recipe_owners"] == [_recipe_owner()]
    assert summary["gpu_ownership"] == [
        {"gpu_role": "gpu-a", "owners": ["recipe:candidate"]},
        {"gpu_role": "gpu-b", "owners": ["recipe:candidate"]},
    ]


def test_all_gpu_recipe_selection_maps_to_every_declared_local_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anvil_serving import topology

    monkeypatch.setattr(
        serve_recipes,
        "discover_recipe_containers",
        lambda **_kwargs: {
            "schema": serve_recipes.RECIPE_CONTAINER_INVENTORY_SCHEMA,
            "containers": [
                {
                    "container": "candidate",
                    "model": "org/model",
                    "state": "running",
                    "gpu_selection": ["all"],
                }
            ],
        },
    )
    monkeypatch.setattr(serves, "resolve_topology_path", lambda _path: "topology.toml")
    monkeypatch.setattr(
        topology,
        "load_topology",
        lambda _path: SimpleNamespace(
            gpu_roles=(
                SimpleNamespace(id="gpu-a", uuid="GPU-a"),
                SimpleNamespace(id="gpu-b", uuid="GPU-b"),
            )
        ),
    )

    ownership = serves._unmanaged_recipe_ownership(_mode_serves())

    assert ownership["owners"][0]["gpu_roles"] == ["gpu-a", "gpu-b"]
    assert ownership["owners"][0]["unresolved_gpu_selection"] == []


def test_mode_transition_refuses_unmanaged_recipe_owner_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda containers, _run=None: {container: "absent" for container in containers},
    )

    rc = serves.cmd_mode(
        _mode_serves(),
        "preview",
        "tp2",
        "split",
        _run=lambda argv, **_kwargs: calls.append(argv),
        _recipe_ownership={
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert rc == 1
    assert calls == []
    assert "unmanaged or unresolved recipe-loaded GPU ownership" in capsys.readouterr().err


def test_mode_status_reports_unresolved_recipe_owner_successfully(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda containers, _run=None: {container: "absent" for container in containers},
    )

    rc = serves.cmd_mode(
        _mode_serves(),
        "status",
        None,
        None,
        _recipe_ownership={
            "owners": [_recipe_owner()],
            "discovery_error": None,
            "topology_resolved": True,
        },
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "unresolved"
    assert payload["unresolved"] == [
        {"serve": "recipe:candidate", "state": "unmanaged-recipe-owner"}
    ]
