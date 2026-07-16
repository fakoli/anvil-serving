import json
from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from anvil_serving import serve_recipes
from anvil_serving import serves
from anvil_serving import cli
from anvil_serving.command_tree import COMMAND_TREE


def _serve(name, model):
    return {
        "name": name,
        "container": "container-" + name,
        "port": 30002,
        "model": model,
        "served_name": model,
        "engine": "vllm",
        "health": "/health",
        "groups": [],
        "up": ["docker", "compose", "-f", "compose.yml", "up", "-d", name],
    }


def _registry(direction="promote", managed_serve="heavy", served_name="new-heavy"):
    return {
        "schema": serve_recipes.REGISTRY_SCHEMA,
        "recipe": [{
            "model": "org/new-heavy",
            "serve": {
                "managed_serve": managed_serve,
                "served_model_name": served_name,
            },
            "activation": {
                "heavy": {
                    "plan": "heavy-swap",
                    "direction": direction,
                    "compose_service": managed_serve,
                },
            },
        }],
    }


def _deployment():
    return {"fingerprint": "sha256:test", "contract": {}}


@pytest.fixture
def topology():
    managed = [_serve("heavy", "new-heavy"), _serve("heavy-old", "old-heavy")]
    plans = [{"name": "heavy-swap", "target": "heavy", "rollback": "heavy-old"}]
    return managed, plans


def test_switch_resolves_recipe_and_reuses_guarded_promotion(monkeypatch, topology, capsys):
    managed, plans = topology
    called = {}

    def fake_promote(serves_arg, plans_arg, name, manifest, **kwargs):
        called.update(name=name, manifest=manifest, **kwargs)
        return 0

    monkeypatch.setattr(serves, "_cmd_promote_unlocked", fake_promote)
    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())
    monkeypatch.setattr(serves, "_validate_promotion_topology", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_configs", lambda plan: None)
    monkeypatch.setattr(serves, "_validate_promotion_profiles", lambda plan: None)
    monkeypatch.setattr(serves, "_router_switch_state", lambda *args, **kwargs: "source")
    monkeypatch.setattr(serves, "_deployed_router_digests", lambda *args, **kwargs: {"config": "a", "profile": "b"})
    monkeypatch.setattr(serves, "_promotion_artifact_digests", lambda *args, **kwargs: {"config": "a"})
    monkeypatch.setattr(serves, "_snapshot_promotion_artifacts", lambda *args, **kwargs: {})
    monkeypatch.setattr(serves, "_running_container_matches_recipe", lambda *args, **kwargs: True)
    rc = serves.cmd_switch(
        managed, plans, _registry(), "heavy", "new-heavy", "serves.toml",
        dry_run=True,
    )

    assert rc == 0
    assert called["name"] == "heavy-swap"
    assert called["rollback"] is False
    assert called["dry_run"] is True
    output = capsys.readouterr().out
    assert "switch heavy -> org/new-heavy (promote plan heavy-swap)" in output
    assert "planned operation:" in output
    assert "planned evidence:" in output


def test_switch_selects_rollback_direction_from_recipe(monkeypatch, topology):
    managed, plans = topology
    called = {}

    def fake_promote(*args, **kwargs):
        called.update(kwargs)
        return 0

    monkeypatch.setattr(serves, "_cmd_promote_unlocked", fake_promote)
    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())
    monkeypatch.setattr(serves, "_validate_promotion_topology", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_configs", lambda plan: None)
    monkeypatch.setattr(serves, "_validate_promotion_profiles", lambda plan: None)
    monkeypatch.setattr(serves, "_router_switch_state", lambda *args, **kwargs: "source")
    monkeypatch.setattr(serves, "_deployed_router_digests", lambda *args, **kwargs: {"config": "a", "profile": "b"})
    monkeypatch.setattr(serves, "_promotion_artifact_digests", lambda *args, **kwargs: {"config": "a"})
    monkeypatch.setattr(serves, "_snapshot_promotion_artifacts", lambda *args, **kwargs: {})
    monkeypatch.setattr(serves, "_running_container_matches_recipe", lambda *args, **kwargs: True)
    registry = _registry(
        direction="rollback", managed_serve="heavy-old", served_name="old-heavy",
    )

    assert serves.cmd_switch(
        managed, plans, registry, "heavy", "new-heavy", "serves.toml",
    ) == 0
    assert called["rollback"] is True


def test_switch_refuses_recipe_without_activation_mapping(topology, capsys):
    managed, plans = topology
    registry = _registry()
    registry["recipe"][0].pop("activation")

    assert serves.cmd_switch(
        managed, plans, registry, "heavy", "new-heavy", "serves.toml",
    ) == 2
    assert "not activation-ready for role 'heavy'" in capsys.readouterr().err


def test_switch_refuses_unsafe_role_name(topology, capsys):
    managed, plans = topology
    assert serves.cmd_switch(
        managed, plans, _registry(), "../../heavy", "new-heavy", "serves.toml",
    ) == 2
    assert "deployment role must use only" in capsys.readouterr().err


def test_switch_refuses_manifest_identity_drift(topology, capsys):
    managed, plans = topology
    registry = _registry(served_name="wrong-model")

    assert serves.cmd_switch(
        managed, plans, registry, "heavy", "new-heavy", "serves.toml",
    ) == 2
    assert "advertises 'new-heavy'" in capsys.readouterr().err


@pytest.mark.parametrize("direction", [None, "forward", True])
def test_recipe_validation_rejects_unsafe_activation_direction(direction):
    recipe = _registry()["recipe"][0]
    recipe["activation"]["heavy"]["direction"] = direction
    with pytest.raises(serve_recipes.RecipeError, match="direction must be"):
        serve_recipes.validate_recipe(recipe)


def test_switch_parser_accepts_positional_recipe():
    parser = serves._build_action_parser("switch")
    args = parser.parse_intermixed_args([
        "heavy", "new-heavy", "--registry", "recipes.toml", "--dry-run",
    ])
    assert args.names == ["heavy"]
    assert args.recipe_selector == "new-heavy"
    assert args.registry == "recipes.toml"
    assert args.dry_run is True


def test_shipped_heavy_activation_metadata_matches_reference_promotion(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    manifest = root / "examples" / "fakoli-dark" / "serves.toml"
    registry = serve_recipes.load_registry(root / "configs" / "serve-recipes.toml")
    managed = serves.load_manifest(manifest)
    plans = serves.load_promotions(manifest)

    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())
    current, plan, rollback, _ = serves.resolve_recipe_activation(
        managed, plans, registry, "heavy", "gemma-4-12B-it-qat-w4a16-ct",
    )
    previous, previous_plan, previous_rollback, _ = serves.resolve_recipe_activation(
        managed, plans, registry, "heavy", "ThinkingCap-Qwen3.6-27B-FP8",
    )

    assert current["serve"]["served_model_name"] == "gemma4-12b-it-w4a16-ct"
    assert (plan, rollback) == ("gemma4-12b-heavy", False)
    assert previous["serve"]["served_model_name"] == "thinkingcap-qwen36-27b-fp8"
    assert (previous_plan, previous_rollback) == ("gemma4-12b-heavy", True)


def test_switch_help_names_role_recipe_and_preview_options():
    help_text = serves._build_action_parser("switch").format_help()
    assert "ROLE [MODEL]" in help_text
    assert "--recipe MODEL" in help_text
    assert "--dry-run" in help_text
    assert "--resume" not in help_text


def test_switch_explicit_configuration_paths_are_not_rewritten():
    assert serves.resolve_manifest_path("deploy/serves.toml") == "deploy/serves.toml"
    assert serves.resolve_recipe_registry_path("catalog/recipes.toml") == "catalog/recipes.toml"


def test_switch_docs_show_preview_apply_and_both_reference_models():
    root = Path(__file__).resolve().parents[1]
    docs = (root / "docs" / "cli" / "serves.md").read_text(encoding="utf-8")
    assert "serves switch heavy\n" in docs
    assert "serves switch heavy gemma-4-12B-it-qat-w4a16-ct --dry-run" in docs
    assert "serves switch heavy gemma-4-12B-it-qat-w4a16-ct --confirm" in docs
    assert "serves switch heavy ThinkingCap-Qwen3.6-27B-FP8 --confirm" in docs
    assert "normal registry row is intentionally not enough" in docs


def test_switch_manifest_declares_bounded_confirmed_interface():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "docs" / "CLI-COMMAND-MANIFEST.json").read_text(encoding="utf-8")
    )
    record = next(command for command in manifest["commands"] if command["path"] == "serves switch")
    flags = {flag for option in record["options"] for flag in option["flags"]}
    assert record["mutation_class"] == "mutate"
    assert record["output_policy"] == "bounded"
    assert {"--confirm", "--dry-run", "--recipe", "--registry"} <= flags
    assert "--follow" not in flags
    recipe_option = next(
        option for option in record["options"] if "--recipe" in option["flags"]
    )
    assert recipe_option["requires_confirmation"] is True


def test_switch_listing_is_confirmation_free_but_recipe_selection_is_guarded():
    serves_node = next(node for node in COMMAND_TREE.nodes if node.name == "serves")
    switch_node = next(node for node in serves_node.children if node.name == "switch")
    assert cli._requires_confirmation(switch_node, ()) is False
    assert cli._requires_confirmation(
        switch_node, ("heavy", "--recipe", "gpt-oss-120b")
    ) is True
    assert cli._requires_confirmation(
        switch_node, ("heavy", "gpt-oss-120b")
    ) is True
    assert cli._requires_confirmation(
        switch_node, ("heavy", "--manifest", "serves.toml")
    ) is False


def test_direct_promotions_share_one_transaction_lock(monkeypatch):
    locks = []

    @contextmanager
    def fake_lock(role):
        locks.append(role)
        yield

    monkeypatch.setattr(serves, "_switch_role_lock", fake_lock)
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *args, **kwargs: 0)

    assert serves.cmd_promote([], [], "first", "serves.toml") == 0
    assert serves.cmd_promote([], [], "second", "serves.toml") == 0
    assert locks == ["promotion", "promotion"]


def test_switch_reference_plan_has_finite_timeouts_and_automatic_rollback():
    root = Path(__file__).resolve().parents[1]
    plans = serves.load_promotions(root / "examples" / "fakoli-dark" / "serves.toml")
    plan = next(item for item in plans if item["name"] == "gemma4-12b-heavy")
    assert plan["drain_timeout"] > 0
    assert plan["startup_timeout"] > 0
    assert plan["rollback_startup_timeout"] > 0
    assert plan["rollback"] == "heavy-thinkingcap-rollback"
    assert plan["rollback_gate"]


def test_switch_paths_and_dispatch_are_platform_neutral(monkeypatch, topology):
    managed, plans = topology
    calls = []
    monkeypatch.setattr(serves, "_cmd_promote_unlocked", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)
    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())
    monkeypatch.setattr(serves, "_validate_promotion_topology", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_configs", lambda plan: None)
    monkeypatch.setattr(serves, "_validate_promotion_profiles", lambda plan: None)
    monkeypatch.setattr(serves, "_router_switch_state", lambda *args, **kwargs: "source")
    assert serves.resolve_recipe_registry_path("C:\\operator\\recipes.toml") == "C:\\operator\\recipes.toml"
    assert serves.resolve_recipe_registry_path("/operator/recipes.toml") == "/operator/recipes.toml"
    assert serves.cmd_switch(
        managed, plans, _registry(), "heavy", "new-heavy", "serves.toml", dry_run=True,
    ) == 0
    assert calls[0][1]["dry_run"] is True


def test_switch_refuses_invalid_topology_before_target_noop(monkeypatch, topology, capsys):
    managed, plans = topology
    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())

    def invalid_topology(*_args):
        raise ValueError("target tier model mismatch")

    monkeypatch.setattr(serves, "_validate_promotion_topology", invalid_topology)
    monkeypatch.setattr(
        serves, "_cmd_promote_unlocked",
        lambda *args, **kwargs: pytest.fail("invalid plan promoted"),
    )
    assert serves.cmd_switch(
        managed, plans, _registry(), "heavy", "new-heavy", "serves.toml", dry_run=True,
    ) == 2
    assert "target tier model mismatch" in capsys.readouterr().err


def test_existing_promote_leaf_retains_controller_tool_parity():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "docs" / "CLI-COMMAND-MANIFEST.json").read_text(encoding="utf-8")
    )
    record = next(command for command in manifest["commands"] if command["path"] == "serves promote")
    assert record["remote_operation"]["tool"] == "serves_promote"
    assert record["remote_operation"]["confirmed_arguments"] == {"human_approved": True}


def test_registry_loader_rejects_malformed_recipe_rows(tmp_path):
    path = tmp_path / "recipes.toml"
    path.write_text('schema = "anvil-serving.serve-recipes/v1"\nrecipe = ["bad"]\n')
    with pytest.raises(serve_recipes.RecipeError, match="array of recipe tables"):
        serve_recipes.load_registry(path)


def test_compose_activation_binds_effective_image_command_env_gpu_and_port():
    recipe = {
        "model": "org/model",
        "hardware": {"gpu_uuid": "GPU-1"},
        "serve": {
            "image": "image@sha256:abc",
            "port": 30002,
            "env": ["MODE=safe"],
            "flags": ["--revision deadbeef", "--served-model-name model"],
        },
    }
    serve = _serve("heavy", "model")
    service = {
        "image": "image@sha256:abc",
        "container_name": serve["container"],
        "command": ["serve", "org/model", "--revision", "deadbeef", "--served-model-name", "model"],
        "environment": {"MODE": "safe", "HF_TOKEN": "secret"},
        "shm_size": "1024",
        "deploy": {"resources": {"reservations": {"devices": [{"device_ids": ["GPU-1"]}]}}},
        "ports": [{"host_ip": "127.0.0.1", "target": 30002, "published": "30002"}],
    }

    compose_hash = "a" * 64

    def run(argv, **_kwargs):
        if "--hash" in argv:
            return SimpleNamespace(returncode=0, stdout="heavy %s\n" % compose_hash, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"services": {"heavy": service}}), stderr="")

    deployment = serves._compose_service_for_recipe(
        serve, recipe, {"compose_service": "heavy"}, _run=run,
    )
    assert deployment["fingerprint"].startswith("sha256:")
    assert deployment["contract"]["shm_size"] == 1024
    assert deployment["contract"]["entrypoint"] is None
    exposed = dict(service, ports=[
        *service["ports"],
        {"host_ip": "0.0.0.0", "target": 30002, "published": "31000"},
    ])

    def exposed_run(argv, **_kwargs):
        if "--hash" in argv:
            return SimpleNamespace(returncode=0, stdout="heavy %s\n" % compose_hash, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"services": {"heavy": exposed}}), stderr="")

    with pytest.raises(serve_recipes.RecipeError, match="exactly the reviewed loopback"):
        serves._compose_service_for_recipe(
            serve, recipe, {"compose_service": "heavy"}, _run=exposed_run,
        )
    drifted = dict(service, image="other:image")

    def drift_run(argv, **_kwargs):
        if "--hash" in argv:
            return SimpleNamespace(returncode=0, stdout="heavy %s\n" % compose_hash, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"services": {"heavy": drifted}}), stderr="")

    with pytest.raises(serve_recipes.RecipeError, match="effective Compose image"):
        serves._compose_service_for_recipe(
            serve, recipe, {"compose_service": "heavy"}, _run=drift_run,
        )


def test_promotion_profiles_may_differ_only_on_affected_tiers(tmp_path):
    forward = tmp_path / "forward.json"
    rollback = tmp_path / "rollback.json"
    base = [
        {"tier_id": "fast-local", "work_class": "chat", "decision": "allow"},
        {"tier_id": "heavy-local", "work_class": "chat", "decision": "allow"},
    ]
    forward.write_text(json.dumps({"entries": base}))
    rollback.write_text(json.dumps({"entries": [base[0], {**base[1], "decision": "deny"}]}))
    plan = {
        "router_profile": str(forward),
        "rollback_router_profile": str(rollback),
        "affected_tiers": ["heavy-local"],
    }
    serves._validate_promotion_profiles(plan)
    rollback.write_text(json.dumps({"entries": [{**base[0], "decision": "deny"}, base[1]]}))
    with pytest.raises(ValueError, match="unaffected router profile entries differ"):
        serves._validate_promotion_profiles(plan)


def test_switch_is_noop_when_target_router_and_exact_serve_are_active(monkeypatch, topology):
    managed, plans = topology
    monkeypatch.setattr(serves, "_compose_service_for_recipe", lambda *args, **kwargs: _deployment())
    monkeypatch.setattr(serves, "_validate_promotion_topology", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_configs", lambda plan: None)
    monkeypatch.setattr(serves, "_validate_promotion_profiles", lambda plan: None)
    monkeypatch.setattr(serves, "_router_switch_state", lambda *args, **kwargs: "target")
    monkeypatch.setattr(serves, "docker_state", lambda *args, **kwargs: "running")
    monkeypatch.setattr(serves, "_health", lambda *args, **kwargs: 200)
    monkeypatch.setattr(serves, "_serve_identity_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(serves, "_running_container_matches_recipe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        serves, "_cmd_promote_unlocked", lambda *args, **kwargs: pytest.fail("already-active switch promoted"),
    )
    assert serves.cmd_switch(
        managed, plans, _registry(), "heavy", "new-heavy", "serves.toml", dry_run=True,
    ) == 0


def test_switch_operation_redirects_gate_evidence_out_of_historical_findings():
    plan = {
        "name": "heavy-swap",
        "gate": [{"name": "functional", "json_out": "docs/findings/old.json"}],
        "rollback_gate": [{"name": "rollback"}],
    }
    promotions, selected, operation, journal = serves._operation_promotion(
        [plan], "heavy-swap", "heavy", {"model": "org/model"}, False,
        "sha256:test", "serves.toml", True,
    )
    assert promotions[0] is selected
    assert selected["gate"][0]["json_out"].startswith(operation["evidence_dir"])
    assert "docs/findings" not in selected["gate"][0]["json_out"]
    assert journal.endswith("journal.json")


def test_running_container_must_match_recipe_launch_inputs():
    recipe = {
        "model": "org/model",
        "hardware": {"gpu_uuid": "GPU-1"},
        "serve": {
            "image": "image@sha256:abc",
            "port": 30002,
            "env": ["MODE=safe"],
            "flags": ["--revision deadbeef", "--served-model-name model"],
        },
    }
    serve = _serve("heavy", "model")
    inspect = [{
        "Config": {
            "Image": recipe["serve"]["image"],
            "Cmd": ["serve", "org/model", "--revision", "deadbeef", "--served-model-name", "model"],
            "Entrypoint": ["vllm"],
            "User": "",
            "WorkingDir": "",
            "Env": ["MODE=safe", "HF_TOKEN=secret"],
            "Labels": {
                "com.docker.compose.service": "heavy",
                "com.docker.compose.config-hash": "a" * 64,
            },
        },
        "HostConfig": {
            "IpcMode": "host",
            "PidMode": "",
            "Privileged": False,
            "ReadonlyRootfs": False,
            "UTSMode": "",
            "SecurityOpt": [],
            "RestartPolicy": {"Name": "unless-stopped"},
            "ShmSize": 1024,
            "DeviceRequests": [{"DeviceIDs": ["GPU-1"]}],
            "PortBindings": {"30002/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30002"}]},
        },
        "Mounts": [{
            "Type": "volume",
            "Name": "project_cache",
            "Destination": "/cache",
            "RW": True,
        }],
    }]
    deployment = {
        "fingerprint": "sha256:test",
        "contract": {
            "service": "heavy",
            "compose_hash": "a" * 64,
            "compose_hash_verifiable": True,
            "cap_add": [],
            "cap_drop": [],
            "devices": [],
            "entrypoint": ["vllm"],
            "environment": {"MODE": "safe"},
            "ipc": "host",
            "network_mode": None,
            "pid": "",
            "ports": [["127.0.0.1", 30002, "30002", "tcp"]],
            "privileged": False,
            "read_only": False,
            "restart": "unless-stopped",
            "security_opt": [],
            "shm_size": "1024",
            "user": "",
            "sysctls": {},
            "ulimits": {},
            "uts": "",
            "volumes": [{
                "type": "volume", "source": "cache", "target": "/cache",
                "read_only": False,
            }],
            "working_dir": "",
        },
    }

    def run(_argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(inspect), stderr="")

    assert serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)
    deployment["contract"]["entrypoint"] = None
    inspect[0]["Config"]["Entrypoint"] = ["image-default"]
    assert serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)
    inspect[0]["HostConfig"]["PortBindings"]["30002/tcp"].append(
        {"HostIp": "0.0.0.0", "HostPort": "31000"}
    )
    assert not serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)
    inspect[0]["HostConfig"]["PortBindings"]["30002/tcp"].pop()
    deployment["contract"]["compose_hash_verifiable"] = False
    inspect[0]["Config"]["Labels"]["com.docker.compose.config-hash"] = "b" * 64
    assert serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)
    inspect[0]["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    assert not serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)
    inspect[0]["HostConfig"]["CapAdd"] = None
    inspect[0]["Config"]["Cmd"][3] = "stale-revision"
    assert not serves._running_container_matches_recipe(serve, recipe, deployment, _run=run)


def test_router_switch_state_requires_an_exact_config_and_profile_pair(tmp_path, monkeypatch):
    forward_config = tmp_path / "forward.toml"
    reverse_config = tmp_path / "reverse.toml"
    forward_profile = tmp_path / "forward.json"
    reverse_profile = tmp_path / "reverse.json"
    forward_config.write_text('[router]\nmapping_version = "forward"\n')
    reverse_config.write_text('[router]\nmapping_version = "reverse"\n')
    forward_profile.write_text('{"entries":[{"tier_id":"heavy","decision":"allow"}]}')
    reverse_profile.write_text('{"entries":[{"tier_id":"heavy","decision":"deny"}]}')
    plan = {
        "router_config": str(forward_config),
        "router_profile": str(forward_profile),
        "rollback_router_config": str(reverse_config),
        "rollback_router_profile": str(reverse_profile),
    }
    forward = {
        "config": serves._artifact_digest(forward_config, "config"),
        "profile": serves._artifact_digest(forward_profile, "profile"),
    }
    reverse = {
        "config": serves._artifact_digest(reverse_config, "config"),
        "profile": serves._artifact_digest(reverse_profile, "profile"),
    }
    monkeypatch.setattr(serves, "_deployed_router_digests", lambda *args, **kwargs: forward)
    assert serves._router_switch_state(plan, False) == "target"
    assert serves._router_switch_state(plan, True) == "source"
    monkeypatch.setattr(
        serves, "_deployed_router_digests",
        lambda *args, **kwargs: {"config": forward["config"], "profile": reverse["profile"]},
    )
    assert serves._router_switch_state(plan, False) == "drift"


def test_router_cas_advances_so_post_promote_failure_can_rollback(tmp_path, monkeypatch):
    forward_config = tmp_path / "forward.toml"
    reverse_config = tmp_path / "reverse.toml"
    forward_profile = tmp_path / "forward.json"
    reverse_profile = tmp_path / "reverse.json"
    forward_config.write_text('[router]\nmapping_version = "forward"\n')
    reverse_config.write_text('[router]\nmapping_version = "reverse"\n')
    forward_profile.write_text('{"entries":[]}')
    reverse_profile.write_text('{"entries":[]}')
    plan = {
        "name": "heavy-swap",
        "target": "heavy",
        "rollback": "heavy-old",
        "affected_tiers": ["heavy-local"],
        "router_config": str(forward_config),
        "router_profile": str(forward_profile),
        "rollback_router_config": str(reverse_config),
        "rollback_router_profile": str(reverse_profile),
        "gate": [],
        "rollback_gate": [],
        "drain_timeout": 1,
        "startup_timeout": 1,
        "rollback_startup_timeout": 1,
        "poll_interval": 0.01,
    }
    source = {
        "config": serves._artifact_digest(reverse_config, "config"),
        "profile": serves._artifact_digest(reverse_profile, "profile"),
    }
    target = {
        "config": serves._artifact_digest(forward_config, "config"),
        "profile": serves._artifact_digest(forward_profile, "profile"),
    }
    state = {"router": dict(source)}
    plan["_expected_router_digests"] = dict(source)
    plan["_expected_artifact_digests"] = serves._promotion_artifact_digests(plan)

    def promotion_cli(argv, **_kwargs):
        if argv[:2] == ["router", "promote"] and "--validate-only" not in argv:
            config = argv[argv.index("--config") + 1]
            profile = argv[argv.index("--profile") + 1]
            state["router"] = {
                "config": serves._artifact_digest(config, "config"),
                "profile": serves._artifact_digest(profile, "profile"),
            }
        return 0

    gateway = iter([500, 200])
    monkeypatch.setattr(serves, "_promotion_cli", promotion_cli)
    monkeypatch.setattr(serves, "_promotion_transition_cli", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "_deployed_router_digests", lambda *args, **kwargs: dict(state["router"]))
    monkeypatch.setattr(serves, "cmd_down", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "_await_healthy", lambda *args, **kwargs: True)
    monkeypatch.setattr(serves, "_serve_identity_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(serves, "_gateway_status", lambda *args, **kwargs: next(gateway))

    managed = [_serve("heavy", "new-heavy"), _serve("heavy-old", "old-heavy")]
    assert serves._promotion_transition(
        managed, plan, "serves.toml", rollback=False,
    ) == 1
    assert plan["_expected_router_digests"] == target
    assert serves._promotion_artifact_digests(plan) == plan["_expected_artifact_digests"]
    assert serves._promotion_transition(
        managed, plan, "serves.toml", rollback=True,
    ) == 0
    assert plan["_expected_router_digests"] == source


def test_promotion_config_validation_rejects_unaffected_router_changes(tmp_path):
    forward = tmp_path / "forward.toml"
    reverse = tmp_path / "reverse.toml"
    forward.write_text(
        '[server]\nauth_env = "TOKEN"\n[router]\nmapping_version = "forward"\n'
        '[router.presets]\nchat = ["heavy-local"]\n'
        '[[router.tiers]]\nid = "heavy-local"\nmodel = "new"\n'
        '[[router.tiers]]\nid = "fast-local"\nmodel = "fast"\n'
    )
    reverse.write_text(
        '[server]\nauth_env = "TOKEN"\n[router]\nmapping_version = "reverse"\n'
        '[router.presets]\nchat = ["heavy-local"]\n'
        '[[router.tiers]]\nid = "heavy-local"\nmodel = "old"\n'
        '[[router.tiers]]\nid = "fast-local"\nmodel = "fast"\n'
    )
    plan = {
        "router_config": str(forward),
        "rollback_router_config": str(reverse),
        "affected_tiers": ["heavy-local"],
    }
    serves._validate_promotion_configs(plan)
    reverse.write_text(reverse.read_text().replace(
        'chat = ["heavy-local"]', 'chat = ["fast-local"]',
    ))
    with pytest.raises(ValueError, match="outside declared affected tier"):
        serves._validate_promotion_configs(plan)


def test_switch_lock_uses_posix_flock_for_linux_and_macos(tmp_path, monkeypatch):
    calls = []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=1,
        LOCK_NB=2,
        LOCK_UN=4,
        flock=lambda fd, operation: calls.append((fd, operation)),
    )
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(serves.os, "name", "posix")
    monkeypatch.setattr(serves, "config_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    with serves._switch_role_lock("heavy"):
        pass
    assert [operation for _fd, operation in calls] == [3, 4]


def test_switch_lock_uses_windows_byte_range_lock(tmp_path, monkeypatch):
    calls = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda fd, operation, size: calls.append((fd, operation, size)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(serves.os, "name", "nt")
    monkeypatch.setattr(serves, "config_path", lambda *parts: str(tmp_path.joinpath(*parts)))
    with serves._switch_role_lock("heavy"):
        pass
    assert [(operation, size) for _fd, operation, size in calls] == [(1, 1), (2, 1)]


def test_switch_choices_reject_unknown_role_and_show_available(topology, capsys):
    managed, plans = topology
    assert serves.cmd_switch_choices(
        managed, plans, _registry(), "Heavy", "recipes.toml",
    ) == 2
    assert "available: heavy" in capsys.readouterr().err


def test_switch_choices_label_ready_and_blocked_rows(monkeypatch, topology, capsys):
    managed, plans = topology
    registry = _registry()
    blocked = json.loads(json.dumps(registry["recipe"][0]))
    blocked["model"] = "org/blocked-heavy"
    registry["recipe"].append(blocked)

    def resolve(_serves, _promotions, _registry_value, _role, selector, **_kwargs):
        if selector == "org/blocked-heavy":
            raise serve_recipes.RecipeError("compose service drift")
        return _registry_value["recipe"][0], "heavy-swap", False, _deployment()

    monkeypatch.setattr(serves, "resolve_recipe_activation", resolve)
    assert serves.cmd_switch_choices(
        managed, plans, registry, "heavy", "recipes.toml",
    ) == 0
    output = capsys.readouterr().out
    assert "ACTIVATE" in output
    assert "org/new-heavy" in output and "ready" in output
    assert "org/blocked-heavy" in output and "blocked" in output
    assert "compose service drift" in output


def test_uncertain_router_state_blocks_opposite_container_recovery(monkeypatch, topology, capsys):
    managed, plans = topology
    calls = []
    monkeypatch.setattr(serves, "_validate_promotion_topology", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_configs", lambda *args: None)
    monkeypatch.setattr(serves, "_validate_promotion_profiles", lambda *args: None)
    monkeypatch.setattr(
        serves, "_promotion_transition",
        lambda *args, **kwargs: calls.append(kwargs.get("rollback", False)) or 4,
    )
    assert serves._cmd_promote_unlocked(
        managed, plans, "heavy-swap", "serves.toml",
    ) == 1
    assert calls == [False]
    assert "automatic container recovery blocked" in capsys.readouterr().out


def test_promote_compares_deployed_state_to_pretransaction_artifact_digest(
    tmp_path, monkeypatch,
):
    forward_config = tmp_path / "forward.toml"
    reverse_config = tmp_path / "reverse.toml"
    forward_profile = tmp_path / "forward.json"
    reverse_profile = tmp_path / "reverse.json"
    forward_config.write_text('[router]\nmapping_version = "forward"\n')
    reverse_config.write_text('[router]\nmapping_version = "reverse"\n')
    forward_profile.write_text('{"entries":[]}')
    reverse_profile.write_text('{"entries":[]}')
    plan = {
        "target": "heavy",
        "rollback": "heavy-old",
        "affected_tiers": ["heavy-local"],
        "router_config": str(forward_config),
        "router_profile": str(forward_profile),
        "rollback_router_config": str(reverse_config),
        "rollback_router_profile": str(reverse_profile),
        "gate": [],
        "rollback_gate": [],
        "drain_timeout": 1,
        "startup_timeout": 1,
        "rollback_startup_timeout": 1,
        "poll_interval": 0.01,
    }
    source = {
        "config": serves._artifact_digest(reverse_config, "config"),
        "profile": serves._artifact_digest(reverse_profile, "profile"),
    }
    state = {"router": dict(source)}
    plan["_expected_router_digests"] = dict(source)
    plan["_expected_artifact_digests"] = serves._promotion_artifact_digests(plan)

    def promotion_cli(argv, **_kwargs):
        if argv[:2] == ["router", "promote"] and "--validate-only" not in argv:
            forward_config.write_text('[router]\nmapping_version = "tampered"\n')
            state["router"] = {
                "config": serves._artifact_digest(forward_config, "config"),
                "profile": serves._artifact_digest(forward_profile, "profile"),
            }
        return 0

    monkeypatch.setattr(serves, "_promotion_cli", promotion_cli)
    monkeypatch.setattr(serves, "_promotion_transition_cli", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "_deployed_router_digests", lambda *args, **kwargs: dict(state["router"]))
    monkeypatch.setattr(serves, "cmd_down", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "cmd_up", lambda *args, **kwargs: 0)
    monkeypatch.setattr(serves, "_await_healthy", lambda *args, **kwargs: True)
    monkeypatch.setattr(serves, "_serve_identity_ready", lambda *args, **kwargs: True)

    managed = [_serve("heavy", "new-heavy"), _serve("heavy-old", "old-heavy")]
    assert serves._promotion_transition(managed, plan, "serves.toml") == 4


def test_promotion_artifacts_are_snapshotted_into_operation_directory(tmp_path):
    sources = {}
    plan = {}
    for field, suffix in (
        ("router_config", ".toml"),
        ("router_profile", ".json"),
        ("rollback_router_config", ".toml"),
        ("rollback_router_profile", ".json"),
    ):
        path = tmp_path / (field + suffix)
        path.write_text("{}" if suffix == ".json" else "[router]\n")
        plan[field] = str(path)
        sources[field] = str(path)
    operation_dir = tmp_path / "operation"
    recorded = serves._snapshot_promotion_artifacts(plan, str(operation_dir))
    assert recorded == sources
    assert all(Path(plan[field]).parent == operation_dir for field in sources)
    assert all(Path(plan[field]).read_bytes() == Path(source).read_bytes()
               for field, source in sources.items())
    for field in sources:
        Path(plan[field]).chmod(0o600)


def test_switch_accepts_positional_recipe_selector(monkeypatch):
    seen = {}
    monkeypatch.setattr(serves, "resolve_manifest_path", lambda _path: "serves.toml")
    monkeypatch.setattr(serves, "load_manifest", lambda _path: ["serve"])
    monkeypatch.setattr(serves, "load_promotions", lambda _path: ["promotion"])
    monkeypatch.setattr(serves.serve_recipes, "load_registry", lambda _path: {"recipe": []})

    def switch(managed, promotions, registry, role, selector, manifest, **kwargs):
        seen.update(
            managed=managed,
            promotions=promotions,
            registry=registry,
            role=role,
            selector=selector,
            manifest=manifest,
            dry_run=kwargs["dry_run"],
        )
        return 0

    monkeypatch.setattr(serves, "cmd_switch", switch)
    assert serves.main(["switch", "heavy", "thinking-cap", "--dry-run"]) == 0
    assert seen["role"] == "heavy"
    assert seen["selector"] == "thinking-cap"
    assert seen["dry_run"] is True


def test_switch_keeps_recipe_flag_compatibility(monkeypatch):
    seen = {}
    monkeypatch.setattr(serves, "resolve_manifest_path", lambda _path: "serves.toml")
    monkeypatch.setattr(serves, "load_manifest", lambda _path: [])
    monkeypatch.setattr(serves, "load_promotions", lambda _path: [])
    monkeypatch.setattr(serves.serve_recipes, "load_registry", lambda _path: {"recipe": []})
    monkeypatch.setattr(
        serves,
        "cmd_switch",
        lambda _serves, _promotions, _registry, role, selector, _manifest, **_kwargs:
            seen.update(role=role, selector=selector) or 0,
    )
    assert serves.main([
        "switch", "heavy", "--recipe", "thinking-cap", "--dry-run",
    ]) == 0
    assert seen == {"role": "heavy", "selector": "thinking-cap"}


def test_switch_rejects_two_recipe_selectors(capsys):
    assert serves.main([
        "switch", "heavy", "thinking-cap", "--recipe", "other", "--dry-run",
    ]) == 2
    assert "either positional MODEL or --recipe MODEL" in capsys.readouterr().err


def test_positional_switch_requires_dispatcher_confirmation(capsys):
    assert cli.main(["serves", "switch", "heavy", "thinking-cap"]) == 3
    assert "confirmation required" in capsys.readouterr().err


def test_switch_help_leads_with_positional_workflow(capsys):
    assert cli.main(["serves", "switch", "--help"]) == 0
    output = capsys.readouterr().out
    assert "serves switch heavy gpt-oss-120b --dry-run" in output
    assert "ROLE [MODEL]" in output
