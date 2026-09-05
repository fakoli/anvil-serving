"""Replica tiers refuse one-serve lifecycle shortcuts before mutation."""

from __future__ import annotations

import textwrap

import pytest

from anvil_serving import serves
from anvil_serving.router import config as router_config


def _write_config(tmp_path, name: str, *, replica: bool, extra_direct: bool = False):
    endpoint = (
        """
        replicas = [
          { id = "member-a", base_url = "http://127.0.0.1:30000/v1", host_id = "host-a", resource_id = "gpu-a", qualification_ref = "qualification:a" },
          { id = "member-b", base_url = "http://127.0.0.1:30001/v1", host_id = "host-a", resource_id = "gpu-b", qualification_ref = "qualification:b" },
        ]
        replica_identity = { model_revision = "revision-1", engine_version = "engine-1", image_digest = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", config_fingerprint = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
        """
        if replica
        else 'base_url = "http://127.0.0.1:30000/v1"'
    )
    direct = """
        [[router.tiers]]
        id = "direct"
        base_url = "http://127.0.0.1:30002/v1"
        model = "direct-model"
        dialect = "openai"
        context_limit = 4096
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_DIRECT_KEY"
        health_path = "/health"
        model_identity = true
    """ if extra_direct else ""
    direct_route = 'llm.direct = "direct"' if extra_direct else ""
    path = tmp_path / name
    path.write_text(textwrap.dedent(f"""
        [router]
        [[router.tiers]]
        id = "primary"
        model = "primary-model"
        dialect = "openai"
        context_limit = 4096
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_PRIMARY_KEY"
        health_path = "/health"
        model_identity = true
        {endpoint}
        {direct}
        [router.model_routes]
        llm.primary = "primary"
        {direct_route}
    """), encoding="utf-8")
    return str(path)


def _plan(config: str, rollback: str, *, affected=None):
    return {
        "name": "plan",
        "target": "target",
        "rollback": "rollback",
        "affected_tiers": affected or ["primary"],
        "router_config": config,
        "rollback_router_config": rollback,
    }


def _serve(name: str, port: int, model: str):
    return {
        "name": name,
        "container": name + "-container",
        "port": port,
        "served_name": model,
        "model": model,
        "health": "/health",
    }


@pytest.mark.parametrize("count", [0, 1, 2])
def test_up_for_replica_refuses_before_candidate_selection(tmp_path, capsys, count):
    path = _write_config(tmp_path, "replica.toml", replica=True)
    config = router_config.load(path)
    candidates = [_serve("serve-%s" % index, 30000 + index, "primary-model") for index in range(count)]
    for candidate in candidates:
        candidate["router_tier"] = "primary"

    assert serves.cmd_up_for(config, candidates, "llm.primary", path) == 1

    assert capsys.readouterr().err.strip() == "replica_lifecycle_unsupported"


def test_up_for_unknown_alias_preserves_existing_refusal(tmp_path, capsys):
    path = _write_config(tmp_path, "replica.toml", replica=True)
    config = router_config.load(path)

    assert serves.cmd_up_for(config, [], "llm.missing", path) == 2

    assert "unknown alias" in capsys.readouterr().err


def test_promotion_replica_refusal_precedes_global_lock_and_transition(tmp_path, monkeypatch, capsys):
    config = _write_config(tmp_path, "promoted.toml", replica=True)
    rollback = _write_config(tmp_path, "rollback.toml", replica=True)
    plan = _plan(config, rollback)

    def forbidden(*args, **kwargs):
        raise AssertionError("lifecycle mutation was reached")

    monkeypatch.setattr(serves, "_switch_role_lock", forbidden)
    monkeypatch.setattr(serves, "_promotion_transition", forbidden)
    assert serves.cmd_promote([], [plan], "plan", "manifest.toml") == 1

    assert capsys.readouterr().out.strip() == "promotion refused: replica_lifecycle_unsupported"


@pytest.mark.parametrize("rollback", [False, True])
def test_promotion_transition_entry_refuses_replica_before_lifecycle(tmp_path, rollback):
    config = _write_config(tmp_path, "promoted.toml", replica=True)
    plan = _plan(config, config)

    with pytest.raises(serves.ReplicaLifecycleUnsupported) as raised:
        serves._promotion_transition([], plan, "manifest.toml", rollback=rollback)

    assert raised.value.code == "replica_lifecycle_unsupported"


def test_unrelated_replica_does_not_block_affected_direct_tier(tmp_path):
    config = _write_config(tmp_path, "direct-plus-replica.toml", replica=True, extra_direct=True)
    plan = _plan(config, config, affected=["direct"])

    promoted, rolled_back = serves._promotion_lifecycle_configs(plan)

    assert promoted.tier("direct").replicas == ()
    assert rolled_back.tier("direct").replicas == ()


def test_missing_affected_tier_and_unloadable_config_have_fixed_refusals(tmp_path):
    direct = _write_config(tmp_path, "direct.toml", replica=False)
    missing_tier = _plan(direct, direct, affected=["missing"])
    missing_config = _plan(str(tmp_path / "missing.toml"), direct)

    for plan in (missing_tier, missing_config):
        with pytest.raises(serves.ReplicaLifecycleConfigurationUnavailable) as raised:
            serves._promotion_lifecycle_configs(plan)
        assert raised.value.code == "replica_lifecycle_configuration_unavailable"


def test_recipe_activation_reuses_promotion_guard_before_compose_resolution(tmp_path):
    config = _write_config(tmp_path, "replica.toml", replica=True)
    plan = _plan(config, config)
    registry = {
        "recipe": [{
            "model": "synthetic-model",
            "serve": {
                "managed_serve": "target",
                "served_model_name": "primary-model",
            },
            "activation": {
                "role": {
                    "plan": "plan", "direction": "promote", "compose_service": "target",
                },
            },
        }],
    }
    managed = [_serve("target", 30000, "primary-model"), _serve("rollback", 30000, "primary-model")]

    with pytest.raises(serves.ReplicaLifecycleUnsupported) as raised:
        serves.resolve_recipe_activation(managed, [plan], registry, "role", "synthetic-model")

    assert raised.value.code == "replica_lifecycle_unsupported"
