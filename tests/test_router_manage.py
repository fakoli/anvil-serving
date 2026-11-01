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
