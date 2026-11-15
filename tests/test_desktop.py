"""Desktop switching never uses a blind alternating state or raw model starts."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from anvil_serving import cli, desktop


@pytest.mark.skipif(os.environ.get("ANVIL_DESKTOP_LIVE_TEST") != "1",
                    reason="opt-in read-only check against local Docker Desktop")
def test_live_docker_inspection_without_healthcheck():
    rows = desktop._containers(desktop._run)
    if not any(row["health"] is None for row in rows):
        pytest.skip("requires an existing container without a Docker healthcheck")
    assert all(isinstance(row["status"], str) for row in rows)


class Docker:
    def __init__(self):
        self.running = True
        self.calls = []
        self.fail = None
        self.clock = 1000
        self.wall_clock = 100000
        self.rows = [dict(id="a" * 64, name="/model", status="running", restart="no", health=None),
                     dict(id="b" * 64, name="/parked", status="exited", restart="no", health=None)]

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs.get("timeout", 1) > 0
        out = ""
        if argv[0] == "tasklist.exe":
            out = '"com.docker.backend.exe","42"' if self.running else '"explorer.exe","7"'
        elif argv[1:3] == ["context", "inspect"]:
            out = desktop.PIPE
        elif argv[1] == "info":
            if self.fail == "info":
                return SimpleNamespace(returncode=1, stdout="", stderr="engine unreachable")
            out = "engine-id"
        elif argv[1] == "ps":
            out = "\n".join(r["id"] for r in self.rows)
        elif argv[1] == "inspect":
            out = "\n".join(json.dumps(r) for r in self.rows)
        elif argv[1] == "desktop":
            if self.fail == argv[2]:
                return SimpleNamespace(returncode=1, stdout="", stderr="operation failed")
            self.running = argv[2] == "start"
            if not self.running:
                for row in self.rows:
                    row["status"] = "exited"
        else:
            raise AssertionError(argv)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    managed = [dict(name="model", container="model")]
    monkeypatch.setattr(desktop.serves, "load_manifest_set", lambda _: managed)
    docker = Docker()
    monkeypatch.setattr(desktop.time, "monotonic", lambda: docker.clock)
    monkeypatch.setattr(desktop.time, "time", lambda: docker.wall_clock)
    return docker, tmp_path / "state" / "desktop-switch.json"


def test_stop_restore_only_previously_running_through_managed_lifecycle(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "stopped"
    assert json.loads(journal.read_text())["containers"] == [
        dict(id="a" * 64, name="/model", serve="model")]
    restored = []

    def up(managed, names, **kwargs):
        restored.extend(names)
        assert kwargs["wait_for_readiness"] is True
        docker.rows[0]["status"] = "running"
        return 0

    monkeypatch.setattr(desktop.serves, "cmd_up", up)
    docker.clock += desktop.COOLDOWN_SECONDS
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "running"
    assert restored == ["model"]
    assert docker.rows[1]["status"] == "exited"
    assert not journal.exists()


def test_preview_confirmation_and_unreachable_engine_do_not_mutate(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="confirm"):
        desktop.switch("toggle", run=docker)
    assert desktop.switch("toggle", dry_run=True, run=docker)["action"] == "stop"
    assert not journal.exists()
    docker.fail = "info"
    with pytest.raises(RuntimeError, match="unreachable"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert not any(c[1] == "desktop" for c in docker.calls)


def test_failed_stop_and_failed_restore_retry_same_direction(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.fail = "stop"
    with pytest.raises(RuntimeError, match="operation failed"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert json.loads(journal.read_text())["phase"] == "stopping"
    docker.fail = None
    desktop.switch("toggle", confirm=True, run=docker)
    docker.clock += desktop.COOLDOWN_SECONDS
    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: 1)
    with pytest.raises(RuntimeError, match="Managed restore failed"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert json.loads(journal.read_text())["phase"] == "starting"
    before = len(docker.calls)
    with pytest.raises(RuntimeError, match="Managed restore failed"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert not any(c[1:3] == ["desktop", "stop"] for c in docker.calls[before:])


def test_unmanaged_non_restarting_container_blocks_shutdown(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.rows[1]["status"] = "running"
    with pytest.raises(RuntimeError, match="unmanaged"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert not journal.exists()
    assert docker.running


def test_remote_context_and_replaced_container_are_refused(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    def remote(argv, **kwargs):
        result = docker(argv, **kwargs)
        if argv[1:3] == ["context", "inspect"]:
            result.stdout = "tcp://100.64.0.10:2376"
        return result
    with pytest.raises(ValueError, match="non-local"):
        desktop.switch("toggle", confirm=True, run=remote)
    desktop.switch("stop", confirm=True, run=docker)
    docker.clock += desktop.COOLDOWN_SECONDS
    docker.rows[0]["id"] = "c" * 64
    with pytest.raises(RuntimeError, match="removed/replaced"):
        desktop.switch("start", confirm=True, run=docker)
    assert journal.exists()


def test_double_press_is_locked_and_cli_forwards_confirmation(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    with desktop.serves._switch_role_lock("desktop"):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(desktop.switch, "toggle", confirm=True, run=docker).result()
            assert result["reason"] == "already-switching"
    assert docker.calls == []
    calls = []
    monkeypatch.setattr(desktop, "switch", lambda *a, **k: calls.append((a, k)) or {})
    assert cli.main(["host", "desktop", "toggle", "--confirm"]) == 0
    assert calls[0][1]["confirm"] is True


def test_rapid_presses_are_discarded_without_extending_cooldown(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    desktop.switch("stop", confirm=True, run=docker)
    saved = journal.read_bytes()
    docker.calls.clear()
    for elapsed in (0, 1, 9):
        docker.clock = 1000 + elapsed
        for action in ("toggle", "start", "stop"):
            result = desktop.switch(action, confirm=True, run=docker)
            assert result["reason"] == "cooldown"
            assert result["retry_after_seconds"] == 10 - elapsed
    assert docker.calls == []
    assert journal.read_bytes() == saved
    assert desktop.switch("status", run=docker)["state"] == "stopped"
    assert desktop.switch("toggle", dry_run=True, run=docker)["action"] == "start"
    monkeypatch.setattr(desktop, "_restore", lambda *a: None)
    docker.clock = 1010
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "running"
    assert not journal.exists()
    assert desktop.switch("toggle", confirm=True, run=docker)["reason"] == "cooldown"
    # A reboot with a similar uptime must not reuse the previous boot's cooldown.
    docker.clock = 1015
    docker.wall_clock += 3600
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "stopped"


def test_subprocess_removes_remote_docker_environment(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://100.64.0.10:2376")
    monkeypatch.setenv("DOCKER_CONTEXT", "remote")
    seen = []
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: seen.append(k))
    desktop._run(["docker", "info"])
    assert "DOCKER_HOST" not in seen[0]["env"]
    assert seen[0]["env"]["DOCKER_CONTEXT"] == desktop.CONTEXT


def test_stopped_preview_does_not_create_journal(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.running = False
    assert desktop.switch("toggle", dry_run=True, run=docker)["action"] == "start"
    assert not journal.exists()


def test_failed_stop_rechecks_new_workloads_and_keeps_original_snapshot(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.fail = "stop"
    with pytest.raises(RuntimeError):
        desktop.switch("stop", confirm=True, run=docker)
    original = journal.read_text()
    docker.fail = None
    docker.rows[1]["status"] = "running"
    with pytest.raises(RuntimeError, match="unmanaged"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert journal.read_text() == original
    assert docker.running
    docker.rows[1]["restart"] = "unless-stopped"
    desktop.switch("toggle", confirm=True, run=docker)
    assert len(json.loads(journal.read_text())["containers"]) == 2


@pytest.mark.parametrize("command", [
    ["docker", "--context", "remote", "compose", "up"],
    ["docker", "-c", "remote", "compose", "up"],
    ["docker", "-c=remote", "compose", "up"],
    ["docker", "-cremote", "compose", "up"],
    ["docker", "-Htcp://100.64.0.10:2376", "ps"],
    ["docker-compose", "--host", "ssh://example.test", "up"],
    ["ssh", "example.test", "docker", "start", "model"],
])
def test_remote_selectors_and_wrappers_rejected_before_launch(monkeypatch, command):
    monkeypatch.setattr(desktop.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(ValueError):
        desktop._run(command)


def test_parked_always_restart_container_blocks_shutdown(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.rows[1]["restart"] = "always"
    with pytest.raises(RuntimeError, match="Parked container"):
        desktop.switch("stop", confirm=True, run=docker)
    assert docker.running
    assert not journal.exists()


def test_partially_stopped_saved_always_container_can_retry(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.rows[0]["restart"] = "always"
    docker.fail = "stop"
    with pytest.raises(RuntimeError):
        desktop.switch("stop", confirm=True, run=docker)
    docker.rows[0]["status"] = "exited"
    docker.fail = None
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "stopped"
    assert json.loads(journal.read_text())["containers"][0]["name"] == "/model"


def test_recipe_backed_existing_serve_is_not_rejected(monkeypatch, tmp_path):
    docker, _ = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop.serves, "load_manifest_set", lambda _: [
        dict(name="model", container="model", up=["anvil-serving", "models", "recipes", "load", "model"])
    ])
    assert desktop.switch("stop", dry_run=True, run=docker)["restore"] == ["/model"]


def test_explicit_ai_default_starts_without_prior_running_snapshot_and_retries(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.running = False
    docker.rows[0]["status"] = "exited"
    monkeypatch.setattr(desktop.serves, "load_manifest_set", lambda _: [
        dict(name="model", container="model", groups=["desktop-start"],
             up=["anvil-serving", "models", "recipes", "load", "model"])
    ])
    assert desktop.switch("toggle", dry_run=True, run=docker)["startup"] == ["model"]
    assert not journal.exists()
    restored = []

    def up(managed, names, **kwargs):
        restored.extend(names)
        if len(restored) == 1:
            return 1
        docker.rows[0]["status"] = "running"
        return 0

    monkeypatch.setattr(desktop.serves, "cmd_up", up)
    with pytest.raises(RuntimeError, match="Managed restore failed"):
        desktop.switch("toggle", confirm=True, run=docker)
    assert json.loads(journal.read_text())["containers"][0]["serve"] == "model"
    assert desktop.switch("toggle", confirm=True, run=docker)["state"] == "running"
    assert restored == ["model", "model"]
    assert docker.rows[1]["status"] == "exited"
    assert not journal.exists()


def test_missing_ai_default_is_not_recreated(monkeypatch, tmp_path):
    docker, journal = setup(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop.serves, "load_manifest_set", lambda _: [
        dict(name="missing", container="missing", groups=["desktop-start"])
    ])
    monkeypatch.setattr(desktop.serves, "cmd_up", lambda *a, **k: pytest.fail("must not create"))
    with pytest.raises(RuntimeError, match="startup container is missing"):
        desktop.switch("start", confirm=True, run=docker)
    assert json.loads(journal.read_text())["phase"] == "starting"


@pytest.mark.parametrize("fields", [
    {"runtime": "native"},
    {"up": ["docker", "compose", "up", "-d"]},
])
def test_ai_default_rejects_native_or_recreating_startup(monkeypatch, tmp_path, fields):
    docker, journal = setup(monkeypatch, tmp_path)
    docker.running = False
    monkeypatch.setattr(desktop.serves, "load_manifest_set", lambda _: [
        dict(name="model", container="model", groups=["desktop-start"], **fields)
    ])
    with pytest.raises(ValueError, match="desktop-start requires"):
        desktop.switch("start", confirm=True, run=docker)
    assert not journal.exists()
    assert not any(c[1] == "desktop" for c in docker.calls)
