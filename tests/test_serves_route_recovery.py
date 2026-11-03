"""Managed promotion recovery against the actual durable route authority."""
import json
import os
from pathlib import Path

import pytest

from anvil_serving import router_manage, serves
from anvil_serving import recipe_monitoring, serve_recipes
from anvil_serving.router.config import load_bytes as load_router_config_bytes
from anvil_serving.router.route_updates import RouteUpdateError, RouteUpdateManager, initialize_route_state, route_digest
from tests.router.test_route_state import Runtime
from tests.test_serves import _hot_route_promotion_setup

REAL_REFRESH = serves._refresh_hot_promotion_monitoring

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
            assert lifecycle == []
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


def _fresh_monitoring_activation(tmp_path, plan, managed):
    target = next(item for item in managed if item["name"] == plan["target"])
    recipe = {"model": target["model"], "download": {"repo": target["model"], "revision": "a" * 40},
              "serve": {"engine": target["engine"], "port": target["port"]}}
    registry = tmp_path / "recipes.toml"
    registry.write_text(serve_recipes.format_recipe(recipe), encoding="utf-8")
    activation = {"identity": {"model": target["model"], "revision": "a" * 40,
                   "image": "sha256:" + "b" * 64, "runtime": target["engine"],
                   "hardware": ["GPU-test-device"], "recipe_selector": target["model"],
                   "recipe_sha256": serve_recipes.recipe_digest(recipe), "served_model": target["served_name"]},
                  "endpoint": {"base_url": "http://127.0.0.1:%s/v1" % target["port"], "model": target["served_name"]},
                  "activation": {"registry_path": str(registry), "monitoring_receipt_path": str(tmp_path / "receipt.json"),
                   "observer_base_url": "http://127.0.0.1:19100", "upstream_base_url": "http://127.0.0.1:%s/v1" % target["port"],
                   "container": target["container"]}}
    path = tmp_path / "activation.json"
    path.write_text(json.dumps(activation), encoding="utf-8")
    plan["route_update"]["activation"] = str(path)
    return target, recipe


def test_monitoring_refresh_validates_frozen_recipe_then_publishes(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    target, recipe = _fresh_monitoring_activation(tmp_path, plan, managed)
    monkeypatch.setattr(serves, "_hot_promotion_activation_path",
                        lambda value, *, rollback: value["route_update"]["activation"])
    calls = []
    monkeypatch.setattr(recipe_monitoring, "withdraw", lambda **kwargs: calls.append(("withdraw", kwargs)))
    monkeypatch.setattr(recipe_monitoring, "publish", lambda value, container, **kwargs: calls.append(
        ("publish", value, container, kwargs)) or {"state": "active"})
    assert REAL_REFRESH(plan, target) == {"state": "active"}
    assert calls[0] == ("withdraw", {"port": target["port"], "container": target["container"], "pending": True})
    assert calls[1][0:3] == ("publish", recipe, target["container"])


def test_monitoring_refresh_failure_keeps_hot_route_held(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    _fresh_monitoring_activation(tmp_path, plan, managed)
    monkeypatch.setattr(serves, "_hot_promotion_activation_path",
                        lambda value, *, rollback: value["route_update"]["activation"])
    calls = []
    monkeypatch.setattr(recipe_monitoring, "withdraw", lambda **_kwargs: None)
    monkeypatch.setattr(recipe_monitoring, "publish", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("probe failed")))
    monkeypatch.setattr(serves, "_refresh_hot_promotion_monitoring", REAL_REFRESH)
    monkeypatch.setattr(router_manage, "route_update_request", lambda action, **_kwargs: calls.append(action) or (
        {"preview_id": "a" * 32, "candidate_sha256": route_digest(load_router_config_bytes(Path(plan["router_config"]).read_bytes())),
         "route_revision": 0, "mode": "tier_replacement", "tier_ids": ["primary-local"], "authority_route_revision": 0,
         "operation_phase": "prepared"} if action == "preview" else
        {"transaction_id": "b" * 32, "state": "held", "mode": "tier_replacement", "drain_scope": "chat_route_bindings", "owner_release_authorized": False}))
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_a, **_k: 0)
    assert serves._promotion_transition(managed, plan, manifest, require_candidate=False) == 1
    assert calls == ["preview", "apply"]


def test_resume_republishes_pending_receipt_without_repeating_lifecycle(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    _fresh_monitoring_activation(tmp_path, plan, managed)
    monkeypatch.setattr(serves, "_hot_promotion_activation_path",
                        lambda value, *, rollback: value["route_update"]["activation"])
    monkeypatch.setattr(serves, "_refresh_hot_promotion_monitoring", REAL_REFRESH)
    attempts, lifecycle, route_calls = [], [], []
    monkeypatch.setattr(recipe_monitoring, "withdraw", lambda **_kwargs: None)
    monkeypatch.setattr(recipe_monitoring, "publish", lambda *_a, **_k: attempts.append("publish") or (
        (_ for _ in ()).throw(ValueError("first publish failed")) if len(attempts) == 1 else {"state": "active"}))
    monkeypatch.setattr(serves, "_hot_promotion_owner_evidence", lambda *_a, **_k: {
        "source": "campaign_activation/v1", "binding_sha256": "a" * 64,
        "container_id": "b" * 64, "served_model": "new-heavy"})
    digest = route_digest(load_router_config_bytes(Path(plan["router_config"]).read_bytes()))
    def route(action, **kwargs):
        route_calls.append(action)
        if action == "status":
            return {"state": "held", "mode": "tier_replacement", "transaction_id": "b" * 32,
                    "route_revision": 0, "operation_phase": "prepared"}
        if action == "preview":
            return {"preview_id": "a" * 32, "candidate_sha256": digest, "route_revision": 0,
                    "mode": "tier_replacement", "tier_ids": ["primary-local"], "authority_route_revision": 0,
                    "operation_phase": "prepared"}
        if action == "apply" and kwargs.get("prepare_only"):
            return {"transaction_id": "b" * 32, "state": "held", "mode": "tier_replacement",
                    "drain_scope": "chat_route_bindings", "owner_release_authorized": False}
        if action == "resume":
            return {"preview_id": "c" * 32, "candidate_sha256": digest, "route_revision": 0,
                    "mode": "tier_replacement", "tier_ids": ["primary-local"], "authority_route_revision": 0,
                    "operation_phase": "prepared"}
        return {"route_revision": 1, "effective_route_sha256": digest, "state": "complete",
                "publish_pending": False, "owner_evidence": {"source": "campaign_activation/v1",
                "binding_sha256": "a" * 64, "container_id": "b" * 64, "served_model": "new-heavy"}}
    monkeypatch.setattr(router_manage, "route_update_request", route)
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: lifecycle.append("down") or 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_a, **_k: lifecycle.append("up") or 0)
    assert serves._promotion_transition(managed, plan, manifest, require_candidate=False) == 1
    assert lifecycle == ["down", "up"]
    assert serves._promotion_transition(managed, plan, manifest, require_candidate=False, resume=True) == 0
    assert attempts == ["publish", "publish"]
    assert lifecycle == ["down", "up"]
    assert route_calls[-2:] == ["resume", "apply"]


def test_recreated_hot_target_refreshes_receipt_before_owner_evidence(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    calls, digests = [], {}

    def route(action, **kwargs):
        calls.append(action)
        if action == "preview":
            digests["forward"] = route_digest(load_router_config_bytes(kwargs["candidate_bytes"]))
            return {"preview_id": "a" * 32, "candidate_sha256": digests["forward"],
                    "route_revision": 0, "mode": "tier_replacement", "tier_ids": ["primary-local"],
                    "authority_route_revision": 0, "operation_phase": "prepared"}
        if action == "apply" and kwargs.get("prepare_only"):
            return {"transaction_id": "b" * 32, "state": "held", "mode": "tier_replacement",
                    "drain_scope": "chat_route_bindings", "owner_release_authorized": False}
        if action == "resume":
            return {"preview_id": "c" * 32, "candidate_sha256": digests["forward"],
                    "route_revision": 0, "mode": "tier_replacement", "tier_ids": ["primary-local"],
                    "authority_route_revision": 0, "operation_phase": "prepared"}
        return {"route_revision": 1, "effective_route_sha256": digests["forward"], "state": "complete",
                "publish_pending": False, "owner_evidence": {"source": "campaign_activation/v1",
                "binding_sha256": "a" * 64, "container_id": "b" * 64, "served_model": "new-heavy"}}

    monkeypatch.setattr(router_manage, "route_update_request", route)
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: calls.append("down") or 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_a, **_k: calls.append("up") or 0)
    monkeypatch.setattr(serves, "_refresh_hot_promotion_monitoring",
                        lambda *_a, **_k: calls.append("refresh") or {"state": "active"})
    monkeypatch.setattr(serves, "_hot_promotion_owner_evidence",
                        lambda *_a, **_k: calls.append("owner") or {"source": "campaign_activation/v1",
                            "binding_sha256": "a" * 64, "container_id": "b" * 64, "served_model": "new-heavy"})

    assert serves._promotion_transition(managed, plan, manifest, require_candidate=False) == 0
    assert calls.index("up") < calls.index("refresh") < calls.index("owner") < calls.index("resume")


def test_explicit_prepared_recovery_restores_owner_before_cancelling_hold(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    transaction_id = "d" * 32
    forward = route_digest(load_router_config_bytes(Path(plan["router_config"]).read_bytes()))
    rollback = route_digest(load_router_config_bytes(Path(plan["rollback_router_config"]).read_bytes()))
    calls = []
    status = {"state": "held", "mode": "tier_replacement", "operation_phase": "prepared",
              "transaction_id": transaction_id, "effective_route_sha256": rollback,
              "authority_available": True, "route_revision": 0}

    def route(action, **kwargs):
        calls.append(action)
        if action == "status":
            return dict(status)
        if action == "resume":
            return {"candidate_sha256": forward, "mode": "tier_replacement", "tier_ids": ["primary-local"],
                    "transaction_id": transaction_id, "operation_phase": "prepared", "authority_route_revision": 0}
        if action == "rollback":
            return {"preview_id": "e" * 32, "candidate_sha256": rollback, "mode": "tier_replacement",
                    "tier_ids": ["primary-local"], "transaction_id": transaction_id,
                    "action": "cancel_prepared_update", "authority_route_revision": 0}
        if action == "apply":
            return {"state": "cancelled", "operation_phase": "cancelled", "transaction_id": transaction_id,
                    "route_revision": 0, "effective_route_sha256": rollback}
        pytest.fail("unexpected route action %s" % action)

    monkeypatch.setattr(router_manage, "route_update_request", route)
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: calls.append("down") or 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *_a, **_k: calls.append("up") or 0)
    monkeypatch.setattr(serves, "_refresh_hot_promotion_monitoring",
                        lambda *_a, **_k: calls.append("refresh") or {"state": "active"})
    monkeypatch.setattr(serves, "_hot_promotion_owner_evidence",
                        lambda *_a, **_k: calls.append("owner") or {"served_model": "old-heavy"})

    assert serves._cmd_promote_unlocked(
        managed, [plan], plan["name"], manifest, rollback=True, recover_prepared=transaction_id,
    ) == 0
    assert calls == ["status", "resume", "status", "down", "up", "refresh", "owner", "rollback", "apply"]


def test_prepared_recovery_refuses_mismatched_transaction_without_lifecycle(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    transaction_id = "d" * 32
    rollback = route_digest(load_router_config_bytes(Path(plan["rollback_router_config"]).read_bytes()))
    monkeypatch.setattr(router_manage, "route_update_request", lambda action, **_kwargs: (
        {"state": "held", "mode": "tier_replacement", "operation_phase": "prepared",
         "transaction_id": transaction_id, "effective_route_sha256": rollback,
         "authority_available": True, "route_revision": 0}
        if action == "status" else
        {"candidate_sha256": "f" * 64, "mode": "tier_replacement", "tier_ids": ["primary-local"],
         "transaction_id": transaction_id, "operation_phase": "prepared", "authority_route_revision": 0}
    ))
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: pytest.fail("mismatched hold must not change lifecycle"))
    assert serves._cmd_promote_unlocked(
        managed, [plan], plan["name"], manifest, rollback=True, recover_prepared=transaction_id,
    ) == 1


def test_prepared_recovery_is_idempotently_refused_after_cancellation(tmp_path, monkeypatch):
    manifest, managed, plan = _hot_route_promotion_setup(tmp_path, monkeypatch)
    transaction_id = "d" * 32
    monkeypatch.setattr(router_manage, "route_update_request", lambda *_a, **_k: {
        "state": "cancelled", "mode": "tier_replacement", "operation_phase": "cancelled",
        "transaction_id": transaction_id, "authority_available": True, "route_revision": 0,
    })
    monkeypatch.setattr(serves, "cmd_down", lambda *_a, **_k: pytest.fail("cancelled recovery must not change lifecycle"))
    assert serves._cmd_promote_unlocked(
        managed, [plan], plan["name"], manifest, rollback=True, recover_prepared=transaction_id,
    ) == 1
