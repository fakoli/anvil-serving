"""The stopped authority rebase only runs through its receipt-bound host gate."""
from contextlib import contextmanager
import hashlib
import os
from types import SimpleNamespace

from anvil_serving import router_manage, router_route_rebase
from anvil_serving.router.container_owner import digest


def _arguments():
    return [
        "routes", "rebase", "--old-config", "/protected/old.toml", "--new-config", "/protected/new.toml",
        "--compose", "/protected/successor.yml", "--startup-abort-config", "/protected/abort.json",
        "--current-stop-receipt", "/protected/stop.json", "--helper-image-id", "sha256:" + "b" * 64,
        "--expected-state-sha256", "a" * 64,
    ]


def test_rebase_cli_refuses_implicit_apply_or_preview(capsys):
    assert router_manage.main(_arguments()) == 1
    assert "router route update failed" in capsys.readouterr().err


def test_rebase_host_binds_preview_digest_before_apply(monkeypatch):
    values = {
        "abort_declaration": {"helper_image_id": "sha256:" + "b" * 64, "compose_sha256": "d" * 64},
        "helper_custody": {"helper_image_id": "sha256:" + "e" * 64,
                           "helper_module_sha256": "f" * 64},
        "stopped": {"daemon_id": "daemon", "container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
                    "finished_at": "2026-11-20T00:00:00Z"},
        "abort_receipt": {"receipt": "abort"}, "current_stop_receipt": {"receipt": "stop"},
        "user": "1000:1000",
        "old_mount": {"type": "bind", "source": "/protected/old.toml", "destination": "/run/old"},
        "new_mount": {"type": "bind", "source": "/protected/new.toml", "destination": "/etc/anvil/config.toml"},
        "route_mount": {"type": "volume", "name": "router-state", "destination": "/var/lib/anvil-router"},
        "old_raw": b"old", "new_raw": b"new", "state_path": "/var/lib/anvil-router/routes.json",
    }
    checks = []
    monkeypatch.setattr(router_route_rebase, "_validated_inputs", lambda **_kwargs: checks.append(1) or values)

    observed = []
    @contextmanager
    def helper(*_args, **_kwargs):
        yield "helper-id", "helper-name", observed.append
    monkeypatch.setattr(router_route_rebase, "_native_container", helper)

    preview = {"applied": False, "state_sha256": "a" * 64, "direction": "add", "memory_route_count": 1,
               "route_revision": 3, "effective_route_sha256": "c" * 64,
               "previous_base_config_sha256": "d" * 64, "base_config_sha256": "e" * 64}
    final = {**preview, "applied": True, "state_sha256": "f" * 64}
    plan = router_route_rebase._plan(preview, values)
    calls = []
    def native(_helper, request):
        calls.append(request)
        return final if request["apply"] else preview

    dry = router_route_rebase.run(old_config="old", new_config="new", compose="compose", env_file=None,
        startup_abort_config="abort", current_stop_receipt="stop", helper_image_id="sha256:" + "e" * 64,
        expected_state_sha256="a" * 64,
        dry_run=True, _native_run=native)
    assert dry == {"applied": False, "dry_run": True, "plan": plan, "plan_sha256": digest(plan)}
    assert calls == [{"expected_old_config_sha256": router_route_rebase._sha256(b"old"),
                      "expected_new_config_sha256": router_route_rebase._sha256(b"new"),
                      "expected_state_sha256": "a" * 64,
                      "state_path": "/var/lib/anvil-router/routes.json", "helper_module_sha256": "f" * 64,
                      "apply": False}]
    assert observed == ["exited"]

    calls.clear(); observed.clear(); checks.clear()
    applied = router_route_rebase.run(old_config="old", new_config="new", compose="compose", env_file=None,
        startup_abort_config="abort", current_stop_receipt="stop", helper_image_id="sha256:" + "e" * 64,
        expected_state_sha256="a" * 64,
        expected_plan_sha256=digest(plan), confirm=True, _native_run=native)
    assert applied == {"applied": True, "dry_run": False, "plan_sha256": digest(plan), "result": final}
    assert [call["apply"] for call in calls] == [False, True]
    assert len(checks) == 3 and observed == ["exited"]


def test_validated_inputs_links_distinct_predecessor_to_aborted_successor(tmp_path, monkeypatch):
    old_config = tmp_path / "old.toml"; new_config = tmp_path / "new.toml"
    predecessor_compose = tmp_path / "old-compose.json"; successor_compose = tmp_path / "new-compose.json"
    config = ('[server]\nauth_env = "ROUTER_TOKEN"\napi_keys_path = "/var/lib/anvil-router/keys.sqlite"\n'
              'admission_state_path = "/var/lib/anvil-router/admission.json"\n'
              'route_state_path = "/var/lib/anvil-router/routes.json"\n[router]\n')
    old_config.write_text(config); new_config.write_text(config)
    for path in (old_config, new_config): path.chmod(0o600)
    predecessor_compose.write_text("old"); successor_compose.write_text("new")
    old_sha, new_sha = (hashlib.sha256(path.read_bytes()).hexdigest() for path in (old_config, new_config))
    old_compose_sha, new_compose_sha = (hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in (predecessor_compose, successor_compose))
    predecessor_id, successor_id, image_id = "1" * 64, "2" * 64, "sha256:" + "3" * 64
    stopped = dict(daemon_id="daemon", container_id=successor_id, image_id=image_id,
                   started_at="2026-11-20T00:00:00Z", restart_count=0, finished_at="2026-11-20T00:01:00Z",
                   status="exited", running=False, paused=False, restarting=False, pid=0,
                   compose_project=router_manage.DEFAULT_COMPOSE_PROJECT, compose_service=router_manage.DEFAULT_SERVICE)
    predecessor = {"services": {"router": {"volumes": [
        {"type": "bind", "source": str(old_config), "target": router_manage.DEFAULT_INSTALLED_CONFIG, "read_only": True},
        {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False},
    ]}}, "volumes": {"router-state": {"name": "router-state"}}}
    successor = {"services": {"router": {"image": "fixture:successor", "volumes": [
        {"type": "bind", "source": str(new_config), "target": router_manage.DEFAULT_INSTALLED_CONFIG, "read_only": True},
        {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router"},
    ]}}, "volumes": {"router-state": {"name": "router-state"}}}
    scope = {"sources": [("volume", "router-state")], "directories": ["/synthetic/router-state"],
             "lineages": [("/synthetic/router-state", (0, 0, 0, 0))]}
    receipt = {"authorization": {"expected_configuration_revision": old_sha, "successor": {
        "compose": str(successor_compose), "compose_sha256": new_compose_sha, "image_id": image_id,
        "configuration_revision": new_sha}}, "preview": {"compose_path": str(predecessor_compose),
        "compose_sha256": old_compose_sha, "incarnation": {"image_id": image_id,
        "compose_project": router_manage.DEFAULT_COMPOSE_PROJECT}}}
    monkeypatch.setattr(router_manage, "_container_incarnation", lambda *_args, **_kwargs: stopped)
    monkeypatch.setattr(router_route_rebase.router_startup_abort, "completed_receipt",
        lambda *_args, **_kwargs: ({"compose": str(successor_compose), "compose_sha256": new_compose_sha,
            "expected_image_id": image_id, "configuration_revision": new_sha}, {"abort": True}))
    from anvil_serving import router_current_stop
    monkeypatch.setattr(router_current_stop, "completed_receipt", lambda *_args, **_kwargs: receipt)
    from anvil_serving.router import maintenance
    monkeypatch.setattr(maintenance, "_current_successor", lambda *_args, **_kwargs: predecessor)
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *_args, **_kwargs:
                        {"rendered": successor, "owner_storage": scope})
    monkeypatch.setattr(router_manage, "_owner_storage_scope", lambda *_args, **_kwargs: scope)
    monkeypatch.setattr(router_route_rebase, "_helper_custody", lambda *_args, **_kwargs: {"helper_image_id": "sha256:" + "4" * 64})
    def run(argv, **_kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(returncode=0, stdout=image_id + "\n")
        if argv[:2] == ["docker", "inspect"]:
            return SimpleNamespace(returncode=0, stdout='"%s:%s"' % (os.getuid(), os.getgid()))
        return SimpleNamespace(returncode=0, stdout="/synthetic/router-state\n")
    values = router_route_rebase._validated_inputs(old_config=str(old_config), new_config=str(new_config),
        compose=str(successor_compose), env_file=None, startup_abort_config="abort", current_stop_receipt="stop",
        helper_image_id="sha256:" + "4" * 64, _run=run)
    assert values["stopped"]["container_id"] == successor_id != predecessor_id
    assert values["old_raw"] == old_config.read_bytes() and values["new_raw"] == new_config.read_bytes()
    assert values["new_mount"]["destination"] == router_manage.DEFAULT_INSTALLED_CONFIG
    assert values["route_mount"]["destination"] == "/var/lib/anvil-router"
