from __future__ import annotations

from anvil_serving import serves


def _serve(name: str, container: str) -> dict:
    return {
        "name": name,
        "container": container,
        "port": 8001,
        "health": "/health",
        "model": name,
        "engine": "vllm",
        "runtime": "docker",
    }


def _no_recipe_ownership() -> dict:
    return {
        "owners": [],
        "discovery_error": None,
        "topology_resolved": False,
    }


def test_status_does_not_call_a_declared_rollback_alternate_unmanaged(
    monkeypatch,
) -> None:  # noqa: ANN001
    target = _serve("target", "target-container")
    rollback = _serve("rollback", "rollback-container")
    manifests = [target, rollback]
    monkeypatch.setattr(
        serves,
        "_docker_port_occupants",
        lambda _ports, _run=None: {
            8001: [
                {
                    "container": "target-container",
                    "state": "running",
                    "compose_project": None,
                }
            ]
        },
    )
    monkeypatch.setattr(
        serves,
        "docker_state",
        lambda container, _run=None: (
            "running" if container == "target-container" else "absent"
        ),
    )
    monkeypatch.setattr(serves, "_health", lambda *_args, **_kwargs: 200)
    monkeypatch.setattr(serves, "_gpu_lines", lambda _run=None: [])

    summary = serves.status_summary(
        manifests,
        names=["target", "rollback"],
        _recipe_ownership=_no_recipe_ownership(),
    )

    assert summary["serves"][0]["port_conflicts"] == []
    assert summary["serves"][1]["port_conflicts"] == []


def test_status_still_reports_a_foreign_same_port_container(monkeypatch) -> None:  # noqa: ANN001
    target = _serve("target", "target-container")
    monkeypatch.setattr(
        serves,
        "_docker_port_occupants",
        lambda _ports, _run=None: {
            8001: [
                {
                    "container": "foreign-container",
                    "state": "running",
                    "compose_project": None,
                }
            ]
        },
    )
    monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "absent")
    monkeypatch.setattr(serves, "_gpu_lines", lambda _run=None: [])

    summary = serves.status_summary(
        [target],
        names=["target"],
        _recipe_ownership=_no_recipe_ownership(),
    )

    assert summary["serves"][0]["port_conflicts"] == [
        {
            "container": "foreign-container",
            "state": "running",
            "compose_project": None,
        }
    ]
