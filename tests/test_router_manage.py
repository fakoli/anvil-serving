import base64
import hashlib
import json
import types

import pytest

from anvil_serving import router_manage
from tests.conftest import proc


_TOPOLOGY = """\
schema_version = 1
id = "synthetic"

[[capacity_policies]]
id = "model-capable"
allow_model_workloads = true

[[hosts]]
id = "host-a"
roles = ["serve"]
capacity_policy = "model-capable"

[[runtimes]]
id = "runtime-a"
host = "host-a"
role = "native"
"""


def _topology(tmp_path):
    path = tmp_path / "topology.toml"
    path.write_text(_TOPOLOGY, encoding="utf-8")
    return path


def _native_gate_output(argv, project="anvil-serving"):
    """Synthetic native custody/consumption through the real lifecycle gate."""
    if argv[:3] == ["docker", "inspect", "--format"] and argv[3] == router_manage._CUSTODY_FORMAT:
        return proc(0, json.dumps({"container_id":"a"*64,"image_id":"sha256:"+"b"*64,
            "image_reference":"anvil-serving:synthetic","started_at":"2026-01-01T00:00:00Z",
            "restart_count":0,"compose_project":project,"compose_service":"router","mounts":[]}))
    if argv[:2] == ["docker", "compose"] and "ps" in argv:
        return proc(0, "a"*64 if project == "anvil-serving" else "")
    if argv[:3] == ["docker", "image", "inspect"]:
        return proc(0, "sha256:"+"b"*64)
    if argv[:2] == ["docker", "exec"] and "_local_router_cutover" in argv[-1]:
        return proc(0,json.dumps({"schema":"router-native-cutover/v1","closed":True,"drained":True,
            "configuration_revision":"c"*64,"roster_revision":"d"*64,"generation":1}))
    return None


def _barrier(action, revision, *, drained=True):
    """Synthetic whole-owner transition; never operational roster evidence."""
    return {"scope":"router","result":{"scope":"router","state":"quiesced","durable":True,
        "barrier_token":"e"*64,"configuration_revision":revision,"roster_revision":"d"*64,
        "generation":1,"drained":drained,"cutover_pending":action=="consume","unknown":[],
        "counts":{family:0 if drained else 1 for family in
            ("chat","purpose","audio","memory","media","internal","delivery","maintenance")}}}


def _run(argv, **_kwargs):
    native = _native_gate_output(argv)
    if native is not None:
        return native
    if argv[:2] == ["docker", "inspect"]:
        if "State.Status" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="anvil-serving\n", stderr="")
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_up_env_file_credentials_override_ambient_without_leaking(tmp_path, capsys):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  router:
    image: ${ROUTER_IMAGE:-anvil-serving:test}
    environment:
      ANVIL_ROUTER_TOKEN: ${ANVIL_ROUTER_TOKEN:-}
      ANVIL_MEDIA_CONTROLLER_TOKEN: ${ANVIL_MEDIA_CONTROLLER_TOKEN:?required}
""",
        encoding="utf-8",
    )
    env_path = tmp_path / "router.env"
    env_path.write_text(
        "ANVIL_ROUTER_TOKEN=FILE_VALUE\n"
        "ANVIL_MEDIA_CONTROLLER_TOKEN=FILE_MEDIA_VALUE\n"
        "UNRELATED_TOKEN=FILE_UNRELATED_VALUE\n",
        encoding="utf-8",
    )
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _run(argv, **kwargs)

    assert router_manage.cmd_up(
        str(compose),
        "router",
        env_file=str(env_path),
        environ={
            "ANVIL_ROUTER_TOKEN": "AMBIENT_VALUE",
            "ANVIL_MEDIA_CONTROLLER_TOKEN": "AMBIENT_MEDIA_VALUE",
            "UNRELATED_TOKEN": "AMBIENT_UNRELATED_VALUE",
            "ROUTER_IMAGE": "anvil-serving:candidate",
        },
        _run=run,
    ) == 0

    execution_env = calls[-1][1]["env"]
    assert execution_env["ANVIL_ROUTER_TOKEN"] == "FILE_VALUE"
    assert execution_env["ANVIL_MEDIA_CONTROLLER_TOKEN"] == "FILE_MEDIA_VALUE"
    assert execution_env["UNRELATED_TOKEN"] == "AMBIENT_UNRELATED_VALUE"
    assert execution_env["ROUTER_IMAGE"] == "anvil-serving:candidate"
    output = capsys.readouterr()
    assert "FILE_VALUE" not in output.out + output.err
    assert "AMBIENT_VALUE" not in output.out + output.err


def test_dry_run_reports_exact_target_without_invoking_docker(tmp_path, monkeypatch, capsys):
    compose = tmp_path / "docker-compose.yml"
    env_file = tmp_path / "router.env"
    monkeypatch.setattr(router_manage, "cmd_up", lambda *_args, **_kwargs: pytest.fail("dry run invoked Docker"))

    assert router_manage.main([
        "up", "--compose", str(compose), "--service", "router", "--env-file", str(env_file),
        "--recreate", "--dry-run",
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "action": "up",
        "applied": False,
        "command": [
            "docker", "compose", "--project-name", "anvil-serving",
            "--env-file", str(env_file), "-f", str(compose), "up", "-d",
            "--no-deps", "--force-recreate", "router",
        ],
        "compose": str(compose),
        "compose_project": "anvil-serving",
        "container": "anvil-router",
        "dry_run": True,
        "env_file": str(env_file),
        "recreate": True,
        "service": "router",
    }


def test_up_recreate_replaces_foreign_compose_owner():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        native = _native_gate_output(argv, "primary-node")
        if native is not None:
            return native
        if "State.Status" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(returncode=0, stdout="primary-node\n", stderr="")
        return _run(argv, **kwargs)

    assert router_manage.cmd_up(
        "compose.yml", "router", recreate=True, _run=run
    ) == 0
    assert ["docker", "rm", "-f", "anvil-router"] in calls
    assert calls[-1][:5] == [
        "docker", "compose", "--project-name", "anvil-serving", "-f",
    ]


def test_router_parser_does_not_offer_profile_promotion():
    parser = router_manage._build_parser()
    actions = next(item for item in parser._actions if item.dest == "action")
    assert "promote" not in actions.choices


def test_install_config_migrates_current_tiers_and_verifies_desired(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        """
[router]
[[router.tiers]]
id = "omni-local"
base_url = "http://127.0.0.1:30003/v1"
model = "omni"
dialect = "openai"
context_limit = 65536
privacy = "local"
tool_support = true
auth_env = "ANVIL_OMNI_LOCAL_KEY"
[router.model_routes]
llm.voice = "omni-local"
""",
        encoding="utf-8",
    )
    calls = []

    def transition(action, **kwargs):
        calls.append((action, kwargs.get("tier_id")))
        if kwargs.get("scope") == "router":
            return _barrier(action, hashlib.sha256(config.read_bytes()).hexdigest())
        if action == "status" and calls.count(("status", None)) == 1:
            return {"tiers": [{"tier_id": "fast-local", "ready": True}]}
        if action == "status" and calls.count(("status", None)) == 2:
            raise ValueError("router transition transport failed (RemoteDisconnected)")
        if action == "status":
            return {"tiers": [{"tier_id": "omni-local", "ready": True}]}
        if action == "drain":
            return {"result": {"drained": True}}
        return {"result": {"applied": True}}

    installed = []
    result = router_manage.install_config(
        str(config),
        topology_path=str(_topology(tmp_path)),
        confirm=True,
        dry_run=False,
        _transition=transition,
        _route_status=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            router_manage.RouteUpdateError("route_update_http_error", "not found", status=404)
        ),
        _install=lambda path: installed.append(path) or 0,
        _sleep=lambda _seconds: None,
    )

    assert result["applied"] is True
    assert result["unavailable_tiers"] == []
    assert result["tier_status"] == [{"tier_id": "omni-local", "ready": True}]
    assert calls == [
        ("status", None),
        ("quiesce", None),
        ("drain", None),
        ("consume", None),
        ("status", None),
        ("status", None),
        ("status", None),
    ]
    assert len(installed) == 1
    assert installed[0].config_bytes == config.read_bytes()


def test_install_config_refuses_protected_route_base_drift_before_quiesce_or_write(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text('''
[router]
[[router.tiers]]
id = "omni-local"
base_url = "http://127.0.0.1:30003/v1"
model = "omni"
dialect = "openai"
context_limit = 65536
privacy = "local"
tool_support = true
auth_env = "ANVIL_OMNI_LOCAL_KEY"
[router.model_routes]
llm.voice = "omni-local"
''', encoding="utf-8")
    calls = []
    with pytest.raises(ValueError, match="protected route state"):
        router_manage.install_config(
            str(config), topology_path=str(_topology(tmp_path)), confirm=True, dry_run=False,
            _transition=lambda action, **_kwargs: calls.append(action) or {"tiers": []},
            _route_status=lambda *_args, **_kwargs: {
                "enabled": True, "authority_available": True, "base_config_sha256": "a" * 64,
            },
            _install=lambda *_args, **_kwargs: pytest.fail("must not write protected base config"),
        )
    assert calls == ["status"]


def test_restart_refuses_route_base_drift_before_drain_or_container_stop(monkeypatch):
    monkeypatch.setattr(router_manage, "_mounted_router_boot_config", lambda *_args, **_kwargs: b"router = {}\n")
    monkeypatch.setattr(router_manage, "require_router_drain",
                        lambda *_args, **_kwargs: pytest.fail("drain must not run after route-base drift"))
    def forbidden_run(*_args, **_kwargs):
        pytest.fail("restart must not stop or restart a drifted router")
    assert router_manage.cmd_restart(
        "anvil-router", dry_run=False, _run=forbidden_run,
        _route_status=lambda *_args, **_kwargs: {"enabled": True, "authority_available": True, "base_config_sha256": "a" * 64},
    ) == 1


def test_restart_legacy_route_status_404_does_not_require_mounted_boot_config(monkeypatch):
    monkeypatch.setattr(router_manage, "_mounted_router_boot_config",
                        lambda *_args, **_kwargs: pytest.fail("legacy restart must not capture boot config"))
    monkeypatch.setattr(router_manage, "require_router_drain",
                        lambda *_args, **_kwargs: {"owner_backend": "native-process"})
    calls = []
    def run(argv, **_kwargs):
        calls.append(argv)
        return proc(0, "")
    assert router_manage.cmd_restart(
        "anvil-router", dry_run=False, _run=run,
        _route_status=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            router_manage.RouteUpdateError("route_update_http_error", "not found", status=404)
        ),
    ) == 0
    assert calls == [["docker", "restart", "anvil-router"]]


def test_route_authority_guard_rejects_enabled_status_without_base_digest():
    with pytest.raises(ValueError, match="status is malformed"):
        router_manage._guard_route_authority_base_config(
            b"router = {}\n", _route_status=lambda *_args, **_kwargs: {"enabled": True, "authority_available": True},
        )


def test_mounted_boot_config_reads_bounded_runtime_bytes_when_host_source_is_private():
    raw = b"[router]\n"
    custody = {"container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
               "image_reference": "anvil-serving:synthetic", "started_at": "2026-01-01T00:00:00Z",
               "restart_count": 0, "compose_project": "anvil-serving", "compose_service": "router",
               "mounts": [{"Type": "bind", "Name": "", "Source": "/container-private/router.toml",
                           "Destination": router_manage.DEFAULT_INSTALLED_CONFIG, "RW": False}]}

    def run(argv, **_kwargs):
        if argv[:3] == ["docker", "inspect", "--format"]:
            return proc(0, json.dumps(custody))
        if argv[:3] == ["docker", "image", "inspect"]:
            return proc(0, custody["image_id"])
        if argv[:3] == ["docker", "exec", custody["container_id"]]:
            if "base64" in argv[-1]:
                return proc(0, base64.b64encode(raw).decode())
            if "hashlib" in argv[-1]:
                return proc(0, hashlib.sha256(raw).hexdigest())
        pytest.fail("unexpected runtime config probe")

    assert router_manage._mounted_router_boot_config("anvil-router", _run=run) == raw

def test_managed_route_state_initializer_uses_compose_volume_and_container_config(tmp_path, monkeypatch):
    from anvil_serving import router_manage

    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/var/lib/anvil-router/route-state.json"\n[router]\n')
    state_mount = {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False}
    config_mount = {"type": "bind", "source": str(config), "target": "/etc/anvil/config.toml", "read_only": True}
    roster = {"rendered": {"services": {"router": {"image": "router:test", "volumes": [config_mount, state_mount]}}}}
    captured = {}
    def roster_run(*args, **kwargs):
        captured["roster_owner_state_only"] = kwargs.get("_owner_state_only")
        return roster
    monkeypatch.setattr(router_manage, "_offline_compose_roster", roster_run)

    def run(compose, service, container, code, **kwargs):
        captured.update(compose=compose, service=service, container=container, code=code,
                        run_owner_state_only=kwargs.get("_owner_state_only"))
        return {"initialized": True, "route_revision": 0, "effective_route_sha256": "a" * 64}

    monkeypatch.setattr(router_manage, "_offline_compose_run", run)
    def docker(*args, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "sha256:" + "c" * 64})()
    result = router_manage.initialize_route_state_managed(str(config), str(tmp_path / "compose.yml"), _run=docker)
    assert result["initialized"] is True
    assert captured["service"] == "router" and captured["container"] == "anvil-router"
    assert captured["roster_owner_state_only"] is True
    assert captured["run_owner_state_only"] is True
    assert "/etc/anvil/config.toml" in captured["code"]
    assert "/var/lib/anvil-router/route-state.json" in captured["code"]


def test_current_stop_route_initializer_revalidates_the_exact_successor_before_and_after_helper(tmp_path, monkeypatch):
    from anvil_serving import router_current_stop

    config = tmp_path / "router.toml"
    old_config = tmp_path / "old-router.toml"
    router = '''
[router]
[[router.tiers]]
id = "synthetic"
base_url = "http://127.0.0.1:18000/v1"
model = "synthetic"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "SYNTHETIC_UPSTREAM"
[router.model_routes]
llm.primary = "synthetic"
'''
    old_config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\n' + router)
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/var/lib/anvil-router/route-state.json"\n' + router)
    mounts = [
        {"type": "bind", "source": str(config), "target": router_manage.DEFAULT_INSTALLED_CONFIG, "read_only": True},
        {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False},
    ]
    stopped = {"container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
               "started_at": "2026-01-01T00:00:00Z", "restart_count": 0,
               "compose_project": "anvil-serving", "compose_service": "router"}
    candidate = {"services": {"router": {"image": stopped["image_id"], "volumes": mounts}}}
    predecessor = {"services": {"router": {"image": stopped["image_id"], "volumes": [
        {"type": "bind", "source": str(old_config), "target": router_manage.DEFAULT_INSTALLED_CONFIG, "read_only": True},
        mounts[1],
    ]}}}
    checks = []
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *args, **kwargs: pytest.fail("generic roster must stay strict"))
    monkeypatch.setattr(router_manage, "_compose_execution_env", lambda *args, **kwargs: {"PATH": "/usr/bin"})
    monkeypatch.setattr(router_current_stop, "completed_receipt", lambda *_args: {"stopped": stopped})
    monkeypatch.setattr(router_current_stop, "require_successor", lambda *args, **kwargs: checks.append(args))
    monkeypatch.setattr(router_current_stop, "_snapshot", lambda *args, **kwargs: ({}, {"rendered": predecessor}, candidate, {}))

    def docker(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            return proc(0, stopped["image_id"])
        if "run" in argv:
            return proc(0, json.dumps({"initialized": True, "route_revision": 0, "effective_route_sha256": "d" * 64}))
        pytest.fail("unexpected command")

    assert router_manage.initialize_route_state_managed(
        str(config), str(tmp_path / "compose.yml"), current_stop_receipt=str(tmp_path / "receipt.json"), _run=docker,
    )["initialized"] is True
    assert len(checks) == 2

    candidate["services"]["router"]["image"] = "router:mutable"
    with pytest.raises(ValueError, match="current_stop_route_init_refused"):
        router_manage.initialize_route_state_managed(
            str(config), str(tmp_path / "compose.yml"), current_stop_receipt=str(tmp_path / "receipt.json"), _run=docker,
        )
    candidate["services"]["router"]["image"] = stopped["image_id"]
    old_config.write_text(config.read_text())
    with pytest.raises(ValueError, match="current_stop_route_init_refused"):
        router_manage.initialize_route_state_managed(
            str(config), str(tmp_path / "compose.yml"), current_stop_receipt=str(tmp_path / "receipt.json"), _run=docker,
        )


@pytest.mark.parametrize("lock_mount", [
    {"type": "volume", "source": "route-lock", "target": "/var/lib/anvil-router/route-state.json.lock", "read_only": False},
    {"type": "bind", "source": "/synthetic/route-lock", "target": "/var/lib/anvil-router/route-state.json.lock", "read_only": True},
])
def test_managed_route_state_initializer_refuses_split_or_readonly_lock_mount(tmp_path, monkeypatch, lock_mount):
    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/var/lib/anvil-router/route-state.json"\n[router]\n')
    mounts = [
        {"type": "bind", "source": str(config), "target": router_manage.DEFAULT_INSTALLED_CONFIG, "read_only": True},
        {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False},
        lock_mount,
    ]
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *args, **kwargs: {
        "rendered": {"services": {"router": {"image": "router:test", "volumes": mounts}}}
    })
    monkeypatch.setattr(router_manage, "_offline_compose_run", lambda *args, **kwargs: pytest.fail("must not launch helper"))
    with pytest.raises(ValueError, match="route_state_mount_unknown"):
        router_manage.initialize_route_state_managed(str(config), str(tmp_path / "compose.yml"))


def test_managed_offline_migration_limits_roster_to_owner_state(monkeypatch):
    captured = {}
    monkeypatch.setattr(router_manage, "_compose_execution_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(router_manage, "_offline_compose_run", lambda *args, **kwargs: captured.update(kwargs) or {
        "schema_version": 4, "migrated": True, "backup_schema_version": 1, "offline": True,
    })
    assert router_manage.migrate_router_offline("synthetic.yml", "/var/lib/anvil-serving/router-keys/before.sqlite3") == {
        "schema_version": 4, "migrated": True, "backup_schema_version": 1, "offline": True,
    }
    assert captured["_owner_state_only"] is True


def test_managed_route_state_initializer_refuses_uncovered_container_path(tmp_path, monkeypatch):
    from anvil_serving import router_manage

    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/container-only/routes.json"\n[router]\n')
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *args, **kwargs: {
        "rendered": {"services": {"router": {"image": "router:test", "volumes": [{
            "type": "bind", "source": str(config), "target": "/etc/anvil/config.toml", "read_only": True,
        }]}}}
    })
    monkeypatch.setattr(router_manage, "_offline_compose_run", lambda *args, **kwargs: pytest.fail("must not launch helper"))
    with pytest.raises(ValueError, match="route_state_mount_unknown"):
        router_manage.initialize_route_state_managed(str(config), str(tmp_path / "compose.yml"))


def test_managed_route_state_initializer_refuses_other_config_mount_source(tmp_path, monkeypatch):
    from anvil_serving import router_manage

    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/var/lib/anvil-router/routes.json"\n[router]\n')
    other = tmp_path / "other.toml"
    other.write_text(config.read_text())
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *args, **kwargs: {
        "rendered": {"services": {"router": {"image": "router:test", "volumes": [
            {"type": "bind", "source": str(other), "target": "/etc/anvil/config.toml", "read_only": True},
            {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False},
        ]}}}
    })
    monkeypatch.setattr(router_manage, "_offline_compose_run", lambda *args, **kwargs: pytest.fail("must not launch helper"))
    with pytest.raises(ValueError, match="route_state_config_mount_unknown"):
        router_manage.initialize_route_state_managed(str(config), str(tmp_path / "compose.yml"))


def test_managed_route_state_initializer_refuses_unverified_selected_image(tmp_path, monkeypatch):
    from anvil_serving import router_manage

    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env = "ROUTER_TOKEN"\nroute_state_path = "/var/lib/anvil-router/routes.json"\n[router]\n')
    monkeypatch.setattr(router_manage, "_offline_compose_roster", lambda *args, **kwargs: {
        "rendered": {"services": {"router": {"image": "router:test", "volumes": [
            {"type": "bind", "source": str(config), "target": "/etc/anvil/config.toml", "read_only": True},
            {"type": "volume", "source": "router-state", "target": "/var/lib/anvil-router", "read_only": False},
        ]}}}
    })
    monkeypatch.setattr(router_manage, "_offline_compose_run", lambda *args, **kwargs: pytest.fail("must not launch helper"))
    def docker(*args, **kwargs):
        return type("Result", (), {"returncode": 0, "stdout": "not-an-image-id"})()
    with pytest.raises(ValueError, match="route_state_image_unknown"):
        router_manage.initialize_route_state_managed(str(config), str(tmp_path / "compose.yml"), _run=docker)
