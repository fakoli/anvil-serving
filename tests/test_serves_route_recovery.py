"""Managed promotion recovery against the actual durable route authority."""
import os
from pathlib import Path

import pytest

from anvil_serving import router_manage, serves
from anvil_serving.router.route_updates import RouteUpdateError, RouteUpdateManager, initialize_route_state
from tests.router.test_route_state import Runtime
from tests.test_serves import _hot_route_promotion_setup

pytestmark = pytest.mark.skipif(os.name != "posix", reason="route authority requires POSIX durability")


@pytest.mark.parametrize("phase", ["prepared", "undrained", "committed", "complete", "dead", "manual", "rollback"])
def test_promotion_recovery_preserves_drain_revision_and_owner_proof(tmp_path, monkeypatch, phase):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    base = Path(plan["rollback_router_config"]).read_bytes()
    candidate = Path(plan["router_config"]).read_bytes()
    path = tmp_path / "route-state.json"
    initialize_route_state(base, path)
    runtime = Runtime(base)
    manager = RouteUpdateManager(runtime, base, path)
    manager.restore()
    plan["drain_timeout"] = 0.01
    lifecycle, route_calls, verified = [], [], []

    def owner_evidence(_plan, target, *, rollback=False):
        verified.append((target["served_name"], rollback))
        return {"source": "campaign_activation/v1", "binding_sha256": ("c" if rollback else "a") * 64,
                "container_id": ("d" if rollback else "b") * 64, "served_model": target["served_name"]}

    def route_request(action, **arguments):
        route_calls.append((action, arguments.get("prepare_only")))
        if action == "status":
            return manager.status()
        if action == "preview":
            return manager.preview(arguments["candidate_bytes"], drain_timeout=arguments.get("drain_timeout", 0.01))
        if action == "resume":
            return manager.resume(arguments["transaction_id"], owner_evidence=arguments.get("owner_evidence"))
        if action == "apply":
            return manager.apply(arguments["preview_id"], confirm=arguments["confirm"],
                                 prepare_only=arguments.get("prepare_only", False))
        pytest.fail("unexpected route operation")

    def down(*_args, **_kwargs):
        assert manager.status()["state"] == "held" and runtime.drained
        lifecycle.append("down")
        return 0

    monkeypatch.setattr(router_manage, "route_update_request", route_request)
    monkeypatch.setattr(serves, "_hot_promotion_owner_evidence", owner_evidence)
    monkeypatch.setattr(serves, "cmd_down", down)
    monkeypatch.setattr(serves, "cmd_up", lambda *_args, **_kwargs: lifecycle.append("up") or 0)
    monkeypatch.setattr(serves, "_install_router_config", lambda *_args, **_kwargs: pytest.fail("router restart"))
    try:
        preview = manager.preview(candidate, drain_timeout=0.01)
        if phase == "manual":
            manager.apply(preview["preview_id"], confirm=True)
        else:
            manager.apply(preview["preview_id"], confirm=True, prepare_only=True)
            if phase not in {"prepared", "undrained"}:
                target = next(item for item in managed if item["name"] == plan["target"])
                resumed = manager.resume(manager.status()["transaction_id"], owner_evidence=owner_evidence(plan, target))
                if phase == "committed":
                    runtime.ready = False
                    with pytest.raises(RouteUpdateError, match="identity_unavailable"):
                        manager.apply(resumed["preview_id"], confirm=True)
                    manager.close()
                    runtime = Runtime(base)
                    manager = RouteUpdateManager(runtime, base, path)
                    manager.restore()
                else:
                    manager.apply(resumed["preview_id"], confirm=True)
        verified.clear()
        if phase == "undrained":
            runtime.drained = False
        if phase == "dead":
            monkeypatch.setattr(serves, "docker_state", lambda *_args, **_kwargs: "exited")

        result = serves._promotion_transition(managed, plan, manifest, resume=True, require_candidate=False,
                                             rollback=phase == "rollback")
        status = manager.status()
        if phase in {"undrained", "dead", "manual"}:
            assert result != 0 and not lifecycle
            if phase == "undrained":
                assert status["state"] == "held" and status["route_revision"] == 0
                assert ("apply", True) in route_calls
        else:
            assert result == 0 and status["state"] == "complete"
            assert status["route_revision"] == (2 if phase == "rollback" else 1)
            assert lifecycle == (["down"] if phase in {"prepared", "rollback"} else [])
            assert verified == [("old-heavy", True)] if phase == "rollback" else verified == [("new-heavy", False)]
            assert status["owner_evidence"]["served_model"] == verified[0][0]
            if phase == "committed":
                assert ("apply", True) not in route_calls
    finally:
        manager.close()


def test_failed_hot_promotion_never_automatically_restores_models(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    transitions = []
    monkeypatch.setattr(serves, "_promotion_transition", lambda *args, **kwargs: transitions.append(kwargs) or 1)
    assert serves._cmd_promote_unlocked(managed, [plan], plan["name"], manifest) == 1
    assert len(transitions) == 1
