"""Replica tiers refuse one-serve lifecycle shortcuts before mutation."""

from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from anvil_serving import cli
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


def test_mode_guard_refuses_active_replica_before_any_state_probe(tmp_path, monkeypatch, capsys):
    path = _write_config(tmp_path, "replica.toml", replica=True)
    active = router_config.load(path)

    monkeypatch.setattr(
        serves, "docker_states", lambda *args, **kwargs: pytest.fail("state probe reached"),
    )
    result = serves.cmd_mode(
        [{"name": "target", "router_tier": "primary"}],
        "preview", "target", "restore", active_config=active,
    )

    assert result == 2
    assert capsys.readouterr().err.strip() == (
        "mode transition refused: replica_lifecycle_unsupported"
    )


def test_mode_guard_includes_stopped_gpu_victims_and_restore_members(tmp_path, monkeypatch, capsys):
    path = _write_config(tmp_path, "replica.toml", replica=True)
    active = router_config.load(path)
    target = {"name": "target"}
    victim = {"name": "victim", "router_tier": "primary"}
    restore = {"name": "restore", "router_tier": "primary", "groups": ["split"]}

    monkeypatch.setattr(
        serves,
        "docker_states",
        lambda *args, **kwargs: pytest.fail("state probe reached"),
    )
    monkeypatch.setattr(
        serves.reservations, "is_gpu_inference", lambda serve: serve is victim,
    )
    assert serves.cmd_mode(
        [target, victim], "preview", "target", "split", active_config=active,
    ) == 2
    assert "replica_lifecycle_unsupported" in capsys.readouterr().err

    monkeypatch.setattr(
        serves.reservations, "is_gpu_inference", lambda serve: False,
    )
    assert serves.cmd_mode(
        [target, restore], "preview", "target", "split", active_config=active,
    ) == 2
    assert "replica_lifecycle_unsupported" in capsys.readouterr().err


def test_mode_profile_and_own_router_profiles_require_fixed_active_config(tmp_path, monkeypatch, capsys):
    replica_path = _write_config(tmp_path, "replica.toml", replica=True)
    direct_path = _write_config(tmp_path, "direct.toml", replica=False)
    active = router_config.load(direct_path)
    target = {
        "name": "target", "router_tier": "primary",
        "router_config": replica_path, "rollback_router_config": direct_path,
    }
    profile = {
        "id": "exclusive", "mode": serves.DUAL_GPU_EXCLUSIVE_MODE,
        "exclusive_target": "target", "restore_group": "split",
        "startup_timeout": 1, "poll_interval": 1,
    }

    monkeypatch.setattr(
        serves, "docker_states", lambda *args, **kwargs: pytest.fail("state probe reached"),
    )
    assert serves.cmd_mode([target], "preview", "target", "split") == 2
    assert "replica_lifecycle_configuration_unavailable" in capsys.readouterr().err
    assert serves.cmd_mode(
        [target], "preview", "target", "split", active_config=active,
    ) == 2
    assert "replica_lifecycle_unsupported" in capsys.readouterr().err

    target["router_config"] = direct_path
    target["rollback_router_config"] = replica_path
    assert serves.cmd_mode(
        [target], "preview", "target", "split", active_config=active,
    ) == 2
    assert "replica_lifecycle_unsupported" in capsys.readouterr().err
    assert serves.cmd_profile([target], [profile], "preview", "exclusive", active_config=active) == 2
    assert "replica_lifecycle_unsupported" in capsys.readouterr().err


def test_unrouted_mode_does_not_require_active_config(tmp_path):
    target = {
        "name": "target", "container": "target", "gpu_roles": ["a", "b"],
        "vram_mib": 1, "tensor_parallel_size": 2,
        "operating_mode": serves.DUAL_GPU_EXCLUSIVE_MODE,
    }

    assert serves._guard_mode_replica_lifecycle([target], "target", "split", None) is None


def test_mode_cli_uses_operator_home_active_config_before_state_probe(
    tmp_path, monkeypatch, capsys,
):
    home = tmp_path / "operator-home"
    home.mkdir()
    active = _write_config(home, "router.toml", replica=True)
    manifest = tmp_path / "serves.toml"
    manifest.write_text(textwrap.dedent("""
        [[gpu_roles]]
        id = "compute-a"
        vram_mib = 100
        reserve_mib = 0

        [[gpu_roles]]
        id = "compute-b"
        vram_mib = 100
        reserve_mib = 0

        [[serve]]
        name = "split"
        container = "split"
        runtime = "docker"
        port = 30000
        model = "split-model"
        engine = "vllm"
        gpu_role = "compute-a"
        vram_mib = 50
        residency = "resident"
        router_tier = "primary"
        groups = ["restore"]

        [[serve]]
        name = "target"
        container = "target"
        runtime = "docker"
        port = 30001
        model = "target-model"
        engine = "vllm"
        gpu_roles = ["compute-a", "compute-b"]
        vram_mib = 100
        residency = "on-demand"
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """), encoding="utf-8")
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.setattr(
        serves, "docker_states", lambda *args, **kwargs: pytest.fail("state probe reached"),
    )

    assert serves.main([
        "mode", "preview", "target", "--restore-group", "restore",
        "--manifest", str(manifest),
    ]) == 2

    error = capsys.readouterr().err
    assert str(active) not in error
    assert "replica_lifecycle_unsupported" in error

    assert serves.main([
        "mode", "preview", "target", "--restore-group", "restore",
        "--manifest", str(manifest), "--config", str(active),
    ]) == 2
    error = capsys.readouterr().err
    assert str(active) not in error
    assert "replica_lifecycle_unsupported" in error


@pytest.mark.parametrize(
    "contents", ["[router", "[router]\n[router.model_routes]\nllm.other = \"other\""]
)
def test_mode_cli_refuses_malformed_or_incomplete_active_config_before_state_probe(
    tmp_path, monkeypatch, capsys, contents,
):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(textwrap.dedent("""
        [[gpu_roles]]
        id = "compute-a"
        vram_mib = 100
        reserve_mib = 0
        [[gpu_roles]]
        id = "compute-b"
        vram_mib = 100
        reserve_mib = 0
        [[serve]]
        name = "split"
        container = "split"
        runtime = "docker"
        port = 30000
        model = "split-model"
        engine = "vllm"
        gpu_role = "compute-a"
        vram_mib = 50
        residency = "resident"
        router_tier = "primary"
        groups = ["restore"]
        [[serve]]
        name = "target"
        container = "target"
        runtime = "docker"
        port = 30001
        model = "target-model"
        engine = "vllm"
        gpu_roles = ["compute-a", "compute-b"]
        vram_mib = 100
        residency = "on-demand"
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """), encoding="utf-8")
    bad_config = tmp_path / "private-invalid-router.toml"
    bad_config.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(
        serves, "docker_states", lambda *args, **kwargs: pytest.fail("state probe reached"),
    )

    assert serves.main([
        "mode", "preview", "target", "--restore-group", "restore",
        "--manifest", str(manifest), "--config", str(bad_config),
    ]) == 2
    error = capsys.readouterr().err
    assert "replica_lifecycle_configuration_unavailable" in error
    assert str(bad_config) not in error


def test_mode_cli_missing_default_config_refuses_before_state_probe(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "serves.toml"
    manifest.write_text(textwrap.dedent("""
        [[gpu_roles]]
        id = "compute-a"
        vram_mib = 100
        reserve_mib = 0
        [[gpu_roles]]
        id = "compute-b"
        vram_mib = 100
        reserve_mib = 0
        [[serve]]
        name = "split"
        container = "split"
        runtime = "docker"
        port = 30000
        model = "split-model"
        engine = "vllm"
        gpu_role = "compute-a"
        vram_mib = 50
        residency = "resident"
        router_tier = "primary"
        groups = ["restore"]
        [[serve]]
        name = "target"
        container = "target"
        runtime = "docker"
        port = 30001
        model = "target-model"
        engine = "vllm"
        gpu_roles = ["compute-a", "compute-b"]
        vram_mib = 100
        residency = "on-demand"
        operating_mode = "dual-gpu-exclusive"
        tensor_parallel_size = 2
    """), encoding="utf-8")
    home = tmp_path / "empty-operator-home"
    home.mkdir()
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    monkeypatch.setattr(
        serves, "docker_states", lambda *args, **kwargs: pytest.fail("state probe reached"),
    )

    assert serves.main([
        "mode", "preview", "target", "--restore-group", "restore",
        "--manifest", str(manifest),
    ]) == 2
    assert "replica_lifecycle_configuration_unavailable" in capsys.readouterr().err


def test_mode_direct_active_config_reaches_existing_planning_seam(tmp_path, monkeypatch):
    active = router_config.load(_write_config(tmp_path, "direct.toml", replica=False))
    reached = []
    plan = {
        "mode": serves.DUAL_GPU_EXCLUSIVE_MODE,
        "target": "target",
        "gpu_roles": [],
        "tensor_parallel_size": 2,
        "drain": [], "stop": [], "blocked": [], "unresolved": [],
        "rollback": {"group": "restore", "serves": []},
    }
    monkeypatch.setattr(serves, "docker_states", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(serves, "_unmanaged_recipe_ownership", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(serves, "operating_mode_plan", lambda *_args: plan.copy())
    monkeypatch.setattr(
        serves, "_mode_router_plan", lambda *_args: reached.append(True) or None,
    )

    assert serves.cmd_mode(
        [{"name": "target", "container": "target", "router_tier": "primary"}],
        "preview", "target", "restore", active_config=active,
    ) == 0
    assert reached == [True]


def test_root_cli_remote_mode_config_refuses_before_transport(monkeypatch, capsys):
    remote_plan = SimpleNamespace(
        command=SimpleNamespace(name="serves-mode-preview"),
        transport="controller",
        warnings=(),
    )
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: remote_plan)
    monkeypatch.setattr(
        cli, "execute_plan", lambda *_args, **_kwargs: pytest.fail("transport reached"),
    )

    assert cli.main([
        "serves", "mode", "preview", "target", "--config", "private-router.toml",
    ]) == 2
    assert "not supported for remote preview" in capsys.readouterr().err
