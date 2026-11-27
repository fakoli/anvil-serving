"""Explicit desktop modes never toggle or stop a job-owned media worker."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from anvil_serving import cli, desktop, desktop_modes
from tests.test_desktop import Docker


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    docker = Docker()
    docker.rows = [dict(id=letter * 64, name="/" + name, status=status,
                        restart="unless-stopped", health=None)
                   for letter, name, status in (("a", "secondary", "exited"),
                       ("b", "gateway", "running"), ("c", "lifecycle", "running"),
                       ("d", "other", "exited"))]
    monkeypatch.setattr(desktop.time, "monotonic", lambda: docker.clock)
    monkeypatch.setattr(desktop.time, "time", lambda: docker.wall_clock)
    for filename, names in (("secondary.toml", ["secondary"]),
                            ("media.toml", ["gateway", "lifecycle"]),
                            ("other.toml", ["other"])):
        (tmp_path / filename).write_text("\n".join(
            f'[[serve]]\nname="{name}"\ncontainer="{name}"\nruntime="docker"\n'
            f'port={8100 + i}\nmodel="example-model"\nengine="none"\ngpu_inference=false\nhealth="/health"\n'
            + ('up="docker compose up -d gateway"\n' if name == "gateway" else "")
            for i, name in enumerate(names)), encoding="utf-8")
    (tmp_path / "guard.exe").touch()
    (tmp_path / "guard.toml").touch()
    (tmp_path / "desktop-modes.toml").write_text(
        'schema="anvil-serving.desktop-modes/v1"\n'
        'secondary_manifest="secondary.toml"\nmedia_manifest="media.toml"\n'
        'model_manifests=["other.toml"]\nworker_containers=["worker"]\n'
        'guard_cli="guard.exe"\nguard_config="guard.toml"\n', encoding="utf-8")
    monkeypatch.setattr(desktop_modes, "_guard_safe", lambda _: None)
    monkeypatch.setattr(desktop.serves, "_await_healthy", lambda *a, **k: True)
    monkeypatch.setattr(desktop.serves, "_serve_identity_ready", lambda *a, **k: True)
    operations = []

    def lifecycle(action, owners, names, **kwargs):
        assert kwargs["_run"] is docker
        if action == "down":
            assert kwargs["keep_container"] is True
        operations.append((action, names[:]))
        for owner in owners:
            if action == "down" and len(owner["container"]) == 64:
                next(row for row in docker.rows if row["id"] == owner["container"])["status"] = "exited"
        for row in docker.rows:
            if row["name"].lstrip("/") in names or (action == "up" and "gateway" in names and row["name"] == "/lifecycle"):
                row["status"] = "running" if action == "up" else "exited"
        return 0

    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: lifecycle("up", *a, **k))
    monkeypatch.setattr(desktop.serves, "cmd_down", lambda *a, **k: lifecycle("down", *a, **k))
    return docker, operations


def dynamic_secondary(monkeypatch, tmp_path, *, tier="secondary-tier", model="model-a"):
    (tmp_path / "client-router.env").write_text("ANVIL_ROUTER_TOKEN=synthetic\n", encoding="utf-8")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    registry = tmp_path / "recipes.toml"
    registry.write_text('''schema = "anvil-serving.serve-recipes/v1"

[[recipe]]
model = "model-a"
[recipe.serve]
image = "example.test/alpha"
port = 8100
served_model_name = "model-a"

[[recipe]]
model = "model-b"
[recipe.serve]
image = "example.test/beta"
port = 8100
served_model_name = "model-b"
''', encoding="utf-8")
    for name, served_name, router_tier in (("alpha", "model-a", "secondary-tier"),
                                           ("beta", "model-b", "other-tier")):
        (manifests / ("serves." + name + ".toml")).write_text(
            '[[serve]]\nname="%s"\ncontainer="%s"\nruntime="docker"\nport=8100\n'
            'model="%s"\nserved_name="%s"\nrouter_tier="%s"\nengine="none"\n'
            'gpu_inference=false\nhealth="/health"\nup="anvil-serving models recipes load %s --registry %s --container %s --confirm"\n'
            % (name, name, served_name, served_name, router_tier, served_name, registry.as_posix(), name), encoding="utf-8")
    (tmp_path / "desktop-modes.toml").write_text(
        'schema="anvil-serving.desktop-modes/v1"\nmedia_manifest="media.toml"\n'
        'model_manifests=[]\nworker_containers=["worker"]\nguard_cli="guard.exe"\nguard_config="guard.toml"\n'
        'secondary_router_url="https://router.example.test"\nsecondary_router_alias="llm.secondary"\n'
        'secondary_router_token_file="client-router.env"\nsecondary_router_token_env="ANVIL_ROUTER_TOKEN"\n'
        'secondary_manifest_dir="manifests"\n', encoding="utf-8")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _):
            import json
            return json.dumps({"data": [{"id": tier, "aliases": ["llm.secondary"], "model": model,
                                          "readiness": {"loaded": False}}]}).encode()

    seen = []
    def opener(*_):
        return SimpleNamespace(open=lambda request, timeout: seen.append((request, timeout)) or Response())
    monkeypatch.setattr(desktop_modes.urllib.request, "build_opener", opener)
    return seen


def test_explicit_modes_and_same_mode_are_idempotent(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert [r["status"] for r in docker.rows] == ["running", "exited", "exited", "exited"]
    docker.clock += 11
    previous = ops[:]
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["changed"] is False
    assert ops == previous
    docker.clock += 11
    assert desktop_modes.select_mode("gaming", confirm=True, run=docker)["state"] == "stopped"
    assert not docker.running
    docker.clock += 11
    previous = ops[:]
    assert desktop_modes.select_mode("gaming", confirm=True, run=docker)["changed"] is False
    assert ops == previous
    docker.clock += 11
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["state"] == "running"
    assert [r["status"] for r in docker.rows] == ["exited", "running", "running", "exited"]
    assert ("up", ["secondary"]) not in ops[len(previous):]


@pytest.mark.parametrize("status", ["running", "created", "paused", "restarting"])
@pytest.mark.parametrize("mode", ["gaming", "secondary"])
def test_busy_worker_refused_before_stops(monkeypatch, tmp_path, status, mode):
    docker, ops = setup(monkeypatch, tmp_path)
    docker.rows.append(dict(id="e" * 64, name="/worker", status=status, restart="no", health=None))
    with pytest.raises(RuntimeError, match="worker"):
        desktop_modes.select_mode(mode, confirm=True, run=docker)
    assert ops == [] and docker.running


def test_worker_appearing_during_control_stop_restores_controls(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    original = desktop.serves.cmd_down

    def race(*a, **k):
        result = original(*a, **k)
        if not any(r["name"] == "/worker" for r in docker.rows):
            docker.rows.append(dict(id="e" * 64, name="/worker", status="running", restart="no", health=None))
        return result

    monkeypatch.setattr(desktop.serves, "cmd_down", race)
    with pytest.raises(RuntimeError, match="worker"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert [r["status"] for r in docker.rows] == ["exited", "running", "running", "exited", "running"]
    assert not any("worker" in names for _, names in ops)


def test_preview_missing_target_and_guard_failure_do_not_stop(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        desktop_modes.select_mode("secondary", run=docker)
    assert desktop_modes.select_mode("secondary", dry_run=True, run=docker)["dry_run"] is True
    assert ops == [] and not (tmp_path / "state").exists()
    docker.rows = docker.rows[1:]
    with pytest.raises(RuntimeError, match="missing"):
        desktop_modes.select_mode("secondary", dry_run=True, run=docker)
    with pytest.raises(RuntimeError, match="missing"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert ops == []
    monkeypatch.setattr(desktop_modes, "_guard_safe", lambda _: (_ for _ in ()).throw(RuntimeError("unsafe guard")))
    with pytest.raises(RuntimeError, match="unsafe guard"):
        desktop_modes.select_mode("media", confirm=True, run=docker)
    with pytest.raises(RuntimeError, match="unsafe guard"):
        desktop_modes.select_mode("media", dry_run=True, run=docker)
    assert ops == []


def test_dynamic_secondary_uses_current_exact_router_assignment(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    seen = dynamic_secondary(monkeypatch, tmp_path)
    docker.rows[0]["name"] = "/alpha"
    docker.rows[0]["status"] = "running"
    docker.rows[3]["name"] = "/beta"
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert not any(action == "up" and names == ["alpha"] for action, names in ops)
    assert seen[0][0].full_url.endswith("/v1/models/capabilities?model=llm.secondary")
    assert seen[0][0].get_header("Authorization") == "Bearer synthetic"


def test_stopped_desktop_defers_router_until_start(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    config = tmp_path / "desktop-modes.toml"
    config.write_text(config.read_text().replace("https://router.example.test", "http://127.0.0.1:8000")
                      + 'router_container="test-router"\n')
    docker.running = False
    docker.rows[0]["name"] = "/alpha"
    docker.rows[-1]["name"] = "/beta"
    lookup = desktop_modes._dynamic_secondary
    def running_lookup(profile):
        assert docker.running
        return lookup(profile)
    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", running_lookup)
    assert "target" not in desktop_modes.select_mode("secondary", dry_run=True, run=docker)
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["target"]["name"] == "alpha"


def test_dynamic_input_errors(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path, model=None)
    with pytest.raises(RuntimeError, match="identity.*parked"):
        desktop_modes.select_mode("secondary", dry_run=True, run=docker)
    path = tmp_path / "desktop-modes.toml"
    path.write_text(path.read_text().replace('"https://router.example.test"', '42'))
    with pytest.raises(ValueError, match="router URL"):
        desktop_modes.select_mode("secondary", dry_run=True, run=docker)


def test_dynamic_secondary_starts_parked_exact_router_assignment(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    docker.rows[0]["name"] = "/alpha"
    docker.rows[3]["name"] = "/beta"
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert ("up", ["alpha"]) in ops


def test_dynamic_secondary_selects_each_exact_model_on_a_shared_tier(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path, model="model-b")
    beta = tmp_path / "manifests" / "serves.beta.toml"
    beta.write_text(beta.read_text(encoding="utf-8").replace('router_tier="other-tier"', 'router_tier="secondary-tier"'), encoding="utf-8")
    docker.rows[0]["name"] = "/alpha"
    docker.rows[3]["name"] = "/beta"
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["target"]["served_name"] == "model-b"
    assert ("up", ["beta"]) in ops
    assert not any(action == "up" and names == ["alpha"] for action, names in ops)


@pytest.mark.parametrize("same_service", [True, False])
def test_parked_compose_recreation_requires_same_managed_service(monkeypatch, tmp_path, same_service):
    docker, _ = setup(monkeypatch, tmp_path)
    manifest = tmp_path / "secondary.toml"
    manifest.write_text(manifest.read_text() + 'up="docker compose up -d worker"\n', encoding="utf-8")
    previous_id = docker.rows[0]["id"]
    new_id = "e" * 64
    original_up = desktop.serves.cmd_up
    def recreate(owners, names, **kwargs):
        rc = original_up(owners, names, **kwargs)
        if names == ["secondary"]:
            docker.rows[0]["id"] = new_id
        return rc
    monkeypatch.setattr(desktop.serves, "cmd_up", recreate)
    original_call = desktop._call
    project = desktop.serves._expected_compose_project(desktop_modes._profile()["secondary"][0])
    def labels(argv, **kwargs):
        if argv[:2] == ["docker", "inspect"] and "com.docker.compose.service" in argv[3]:
            return project + ("/worker" if same_service or argv[-1] == previous_id else "/other")
        return original_call(argv, **kwargs)
    monkeypatch.setattr(desktop, "_call", labels)
    if same_service:
        assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["changed"]
    else:
        with pytest.raises(RuntimeError, match="unverified owner"):
            desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert docker.rows[0]["status"] == "running"


def test_dynamic_secondary_starts_absent_managed_backer(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    recipe_loads = []
    docker.rows = [row for row in docker.rows if row["name"] != "/secondary"]
    docker.rows[-1]["name"] = "/beta"
    def load_recipe(binding, target, timeout):
        recipe_loads.append((binding, target, timeout))
        docker.rows.append(dict(id="f" * 64, name="/alpha", status="running", restart="unless-stopped", health=None))
    monkeypatch.setattr(desktop_modes, "_load_recipe", load_recipe)
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert recipe_loads[0][0]["model"] == "model-a"


def test_failed_cold_start_preserves_uncertain_container_and_parks_media(monkeypatch, tmp_path):
    docker, operations = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    docker.rows.pop(0)
    docker.rows[-1]["name"] = "/beta"

    def interrupted_load(*args):
        docker.rows.append(dict(id="f" * 64, name="/alpha", status="running", restart="no", health=None))
        raise RuntimeError("loader interrupted")

    monkeypatch.setattr(desktop_modes, "_load_recipe", interrupted_load)
    with pytest.raises(RuntimeError, match="ownership is uncertain"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert not any(action == "up" for action, _ in operations)
    assert docker.rows[-1]["status"] == "running"


def test_route_change_while_model_starts_rolls_back_exact_target(monkeypatch, tmp_path):
    docker, operations = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    docker.rows[0]["name"] = "/alpha"
    docker.rows[-1]["name"] = "/beta"
    running_lookup = desktop_modes._dynamic_secondary
    def reassign_after_start(profile):
        if ("up", ["alpha"]) in operations:
            return next(m for m in profile["dynamic_models"] if m["container"] == "beta")
        return running_lookup(profile)
    monkeypatch.setattr(desktop_modes, "_dynamic_secondary", reassign_after_start)
    with pytest.raises(RuntimeError, match="assignment changed during"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert ("down", ["alpha"]) in operations
    assert docker.rows[0]["status"] == "exited"


def test_failed_parked_start_recovers_original_container(monkeypatch, tmp_path):
    docker, operations = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    docker.rows[0]["name"] = "/alpha"
    docker.rows[-1]["name"] = "/beta"
    cmd_up = desktop.serves.cmd_up
    def fail_after_start(owners, names, **kwargs):
        result = cmd_up(owners, names, **kwargs)
        return 1 if names == ["alpha"] else result
    monkeypatch.setattr(desktop.serves, "cmd_up", fail_after_start)
    with pytest.raises(RuntimeError, match="Managed start failed"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert docker.rows[0]["status"] == "exited"
    assert ("up", ["gateway"]) in operations


@pytest.mark.parametrize("entrypoint", ["anvil-serving", "python -m anvil_serving.cli"])
def test_dynamic_recipe_load_uses_validated_selector_not_served_name(monkeypatch, tmp_path, entrypoint):
    setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path, model="public-a")
    registry = tmp_path / "recipes.toml"
    registry.write_text(registry.read_text(encoding="utf-8").replace('served_model_name = "model-a"', 'served_model_name = "public-a"'), encoding="utf-8")
    alpha = tmp_path / "manifests" / "serves.alpha.toml"
    alpha.write_text(alpha.read_text(encoding="utf-8").replace('served_name="model-a"', 'served_name="public-a"').replace("anvil-serving models", entrypoint + " models"), encoding="utf-8")
    target = desktop_modes._secondary(desktop_modes._profile())
    binding = desktop_modes._recipe_start_binding(target)
    seen = {}
    monkeypatch.setattr(desktop_modes.subprocess, "run", lambda argv, **kwargs: seen.update(argv=argv, env=kwargs["env"]) or SimpleNamespace(returncode=0))
    desktop_modes._load_recipe(binding, target, 1)
    assert binding["model"] == "model-a"
    assert seen["argv"][6] == "model-a" and seen["argv"][-1] == "--confirm"
    assert seen["env"]["DOCKER_CONTEXT"] == desktop.CONTEXT and seen["env"]["ANVIL_SERVING_NO_HOME_ENV"] == "1"


@pytest.mark.parametrize("tier,model", [("missing-tier", "model-a"), ("secondary-tier", "wrong-model")])
def test_dynamic_secondary_missing_backer_refuses_before_docker_mutations(monkeypatch, tmp_path, tier, model):
    docker, ops = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path, tier=tier, model=model)
    with pytest.raises(RuntimeError, match="unambiguous"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert not ops and all(argv[1] in {"context", "info"} or argv[0] == "tasklist.exe" for argv in docker.calls)


def test_dynamic_secondary_ambiguous_backer_refuses_before_docker_mutations(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    dynamic_secondary(monkeypatch, tmp_path)
    (tmp_path / "manifests" / "serves.duplicate.toml").write_text(
        (tmp_path / "manifests" / "serves.alpha.toml").read_text(encoding="utf-8").replace('name="alpha"', 'name="alpha-two"').replace('container="alpha"', 'container="alpha-two"'),
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="unambiguous"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert not ops and all(argv[1] in {"context", "info"} or argv[0] == "tasklist.exe" for argv in docker.calls)


def test_shared_lock_cooldown_failure_retry_and_cli(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    with desktop.serves._switch_role_lock("desktop"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(desktop_modes.select_mode, "gaming", confirm=True, run=docker).result()
            assert result["reason"] == "already-switching"
    assert ops == []
    original = desktop.serves.cmd_up
    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: 1)
    with pytest.raises(RuntimeError, match="start"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    monkeypatch.setattr(desktop.serves, "cmd_up", original)
    assert desktop_modes.select_mode("secondary", confirm=True, run=docker)["mode"] == "secondary"
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["reason"] == "cooldown"
    seen = []
    monkeypatch.setattr(desktop_modes, "select_mode", lambda *a, **k: seen.append((a, k)) or {})
    assert cli.main(["host", "desktop", "mode", "media", "--confirm"]) == 0
    assert seen[0][0] == ("media",) and seen[0][1]["confirm"]


def test_legacy_snapshot_archived_without_restoring_it(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    legacy = state / "desktop-switch.json"
    legacy.write_text('{"old": "preserve this evidence"}', encoding="utf-8")
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["mode"] == "media"
    assert not legacy.exists()
    assert any(p.read_text(encoding="utf-8") == '{"old": "preserve this evidence"}' for p in state.glob("desktop-switch.superseded-*.json"))
    assert ops == []


def test_wrong_model_identity_is_refused_and_controls_restored(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop.serves, "_serve_identity_ready", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="identity"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert docker.rows[1]["status"] == docker.rows[2]["status"] == "running"
    assert docker.rows[0]["status"] == "exited"
    assert not (tmp_path / "state" / "desktop-switch-cooldown.json").exists()


@pytest.mark.parametrize('advertised', ['public-api', 'example-model'])
def test_secondary_uses_declared_api_identity(monkeypatch, tmp_path, advertised):
    import json
    from tests.conftest import Response
    from anvil_serving.serves import _serve_identity_ready as original
    docker, _ = setup(monkeypatch, tmp_path)
    with (tmp_path / 'secondary.toml').open('a', encoding='utf-8') as f:
        f.write('served_name="public-api"\n')
    def identity(target):
        return original(target, _open=lambda *a, **k: Response(json.dumps({'data':[{'id':advertised}]}).encode()))
    monkeypatch.setattr(desktop.serves, '_serve_identity_ready', identity)
    if advertised == 'public-api':
        assert desktop_modes.select_mode('secondary', confirm=True, run=docker)['mode'] == 'secondary'
    else:
        with pytest.raises(RuntimeError, match='identity'):
            desktop_modes.select_mode('secondary', confirm=True, run=docker)


@pytest.mark.parametrize("mode", ["gaming", "secondary", "media"])
def test_undeclared_running_workload_is_preserved_and_refused(monkeypatch, tmp_path, mode):
    docker, ops = setup(monkeypatch, tmp_path)
    docker.rows.append(dict(id="e" * 64, name="/unmanaged", status="running", restart="unless-stopped", health=None))
    with pytest.raises(RuntimeError, match="Undeclared"):
        desktop_modes.select_mode(mode, confirm=True, run=docker)
    with pytest.raises(RuntimeError, match="Undeclared"):
        desktop_modes.select_mode(mode, dry_run=True, run=docker)
    assert ops == [] and docker.running


def test_worker_assignment_overlap_is_invalid_before_docker_calls(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    config = tmp_path / "desktop-modes.toml"
    config.write_text(config.read_text().replace('["worker"]', '["other"]'), encoding="utf-8")
    with pytest.raises(ValueError, match="overlap"):
        desktop_modes.select_mode("media", confirm=True, run=docker)
    assert not ops and all(argv[1] in {"context", "info"} or argv[0] == "tasklist.exe" for argv in docker.calls)


def test_missing_control_postcondition_and_busy_partial_recovery(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    docker.rows[2]["status"] = "exited"
    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: 0)
    with pytest.raises(RuntimeError, match="control"):
        desktop_modes.select_mode("media", confirm=True, run=docker)
    docker.rows.append(dict(id="e" * 64, name="/worker", status="running", restart="no", health=None))
    with pytest.raises(RuntimeError, match="worker"):
        desktop_modes.select_mode("media", confirm=True, run=docker)


def test_media_releases_model_before_waiting_for_safe_host_memory(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    docker.rows[0]["status"] = "running"
    def guard(_):
        assert docker.rows[0]["status"] == "exited", "Model must release host memory before the guard is checked"
    monkeypatch.setattr(desktop_modes, "_guard_safe", guard)
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["mode"] == "media"


def test_guard_waits_for_fresh_signal_and_reports_persistent_reason(monkeypatch):
    clock = [0]
    replies = [False, True]
    def status(*a, **k):
        import json
        clock[0] += 1
        safe = replies.pop(0) if replies else False
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ok':True, 'data':json.dumps({'safe':safe, 'reason':'signal stale'})}))
    monkeypatch.setattr(desktop_modes.subprocess, "run", status)
    monkeypatch.setattr(desktop_modes.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(desktop_modes.time, "sleep", lambda _: None)
    config = {'guard_cli':'guard.exe', 'guard_config':'guard.toml'}
    desktop_modes._guard_safe(config)
    assert clock[0] == 2
    with pytest.raises(RuntimeError, match="signal stale"):
        desktop_modes._guard_safe(config)


def test_media_waits_for_docker_health_probe_after_http_startup(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    docker.rows[2]["status"] = "exited"
    docker.rows[1]["health"] = "starting"
    monkeypatch.setattr(desktop_modes.time, "sleep", lambda _: docker.rows[1].update(health="healthy"))
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["mode"] == "media"
    assert docker.rows[1]["health"] == "healthy"


def idle_binding(tmp_path):
    with (tmp_path / "desktop-modes.toml").open("a", encoding="utf-8") as f:
        f.write('\n[idle_teardown]\nworker="worker"\ncontroller="lifecycle"\n'
                'service="media-worker"\nmanifest="serves.toml"\nprincipal="operator"\n')


@pytest.mark.parametrize("mode", ["gaming", "secondary"])
def test_owned_idle_worker_released_after_gateway_freeze(monkeypatch, tmp_path, mode):
    docker, ops = setup(monkeypatch, tmp_path)
    idle_binding(tmp_path)
    docker.rows.append(dict(id="e" * 64, name="/worker", status="running", restart="no", health=None))
    def release(profile, *, apply, run):
        assert run is docker
        if apply:
            assert docker.rows[1]["status"] == "exited"
            assert docker.rows[2]["status"] == "running"
            ops.append(("teardown", ["worker"]))
            docker.rows[:] = [r for r in docker.rows if r["name"] != "/worker"]
        return {"idle":True, "applied":apply, "reason":"owned_worker_idle"}
    monkeypatch.setattr(desktop_modes, "_release_idle_worker", release)
    preview = desktop_modes.select_mode(mode, dry_run=True, run=docker)
    assert preview["idle_workers_to_teardown"] == 1
    assert ops == []
    assert desktop_modes.select_mode(mode, confirm=True, run=docker)["mode"] == mode
    assert ("teardown", ["worker"]) in ops
    assert not any(action == "down" and "worker" in names for action,names in ops)


def test_gateway_starting_after_preflight_is_frozen_before_release(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    idle_binding(tmp_path)
    docker.rows[1]['status'] = 'exited'
    docker.rows.append(dict(id='e'*64, name='/worker', status='running', restart='no', health=None))
    original = desktop._containers
    reads = []
    def snapshot(run):
        rows = original(run)
        if not reads:
            docker.rows[1]['status'] = 'running'
        reads.append(True)
        return rows
    def release(profile, *, apply, run):
        if apply:
            assert docker.rows[1]['status'] == 'exited'
            docker.rows[:] = [r for r in docker.rows if r['name'] != '/worker']
        return {'idle':True, 'applied':apply, 'reason':'owned_worker_idle'}
    monkeypatch.setattr(desktop, '_containers', snapshot)
    monkeypatch.setattr(desktop_modes, '_release_idle_worker', release)
    assert desktop_modes.select_mode('secondary', confirm=True, run=docker)['mode'] == 'secondary'
    assert ('down', ['gateway']) in ops


def test_unverified_gateway_stop_refuses_before_teardown(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    idle_binding(tmp_path)
    docker.rows.append(dict(id='e'*64, name='/worker', status='running', restart='no', health=None))
    monkeypatch.setattr(desktop.serves, 'cmd_down', lambda *a, **k: 0)
    def release(profile, *, apply, run):
        assert not apply, 'must verify submission freeze before teardown'
        return {'idle':True, 'applied':False, 'reason':'owned_worker_idle'}
    monkeypatch.setattr(desktop_modes, '_release_idle_worker', release)
    with pytest.raises(RuntimeError, match='gateway'):
        desktop_modes.select_mode('secondary', confirm=True, run=docker)
    assert docker.rows[0]['status'] == 'exited'
    assert docker.rows[-1]['status'] == 'running'


def test_bound_worker_busy_unknown_or_racing_refuses_safely(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    idle_binding(tmp_path)
    docker.rows.append(dict(id="e" * 64, name="/worker", status="running", restart="no", health=None))
    def release(profile, *, apply, run):
        return {"idle":False, "applied":False, "reason":"native_queue_busy"}
    monkeypatch.setattr(desktop_modes, "_release_idle_worker", release)
    with pytest.raises(RuntimeError, match="native_queue_busy"):
        desktop_modes.select_mode("gaming", confirm=True, run=docker)
    assert ops == [] and docker.running
    monkeypatch.setattr(desktop_modes, "_release_idle_worker", lambda *a, **k:
                        {"idle":not k["apply"], "applied":False, "reason":"unfinished_media_jobs"})
    with pytest.raises(RuntimeError, match="unfinished_media_jobs"):
        desktop_modes.select_mode("secondary", confirm=True, run=docker)
    assert docker.rows[1]["status"] == docker.rows[2]["status"] == "running"
    assert docker.rows[-1]["status"] == "running"
    assert not any(action == "up" and names == ["secondary"] for action,names in ops)


def test_media_reselection_keeps_its_idle_worker(monkeypatch, tmp_path):
    docker, ops = setup(monkeypatch, tmp_path)
    idle_binding(tmp_path)
    docker.rows.append(dict(id="e" * 64, name="/worker", status="running", restart="no", health=None))
    monkeypatch.setattr(desktop_modes, "_release_idle_worker", lambda *a, **k: pytest.fail("same-mode worker must stay"))
    assert desktop_modes.select_mode("media", confirm=True, run=docker)["changed"] is False
    assert ops == []
