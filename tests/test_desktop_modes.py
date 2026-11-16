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
        for row in docker.rows:
            if row["name"].lstrip("/") in names or (action == "up" and "gateway" in names and row["name"] == "/lifecycle"):
                row["status"] = "running" if action == "up" else "exited"
        return 0

    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: lifecycle("up", *a, **k))
    monkeypatch.setattr(desktop.serves, "cmd_down", lambda *a, **k: lifecycle("down", *a, **k))
    return docker, operations


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
    assert docker.calls == ops == []


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
