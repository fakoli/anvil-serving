import json
import types

import pytest

from anvil_serving import router_manage


def _run(argv, **_kwargs):
    if argv[:2] == ["docker", "inspect"]:
        if "State.Status" in " ".join(argv):
            return types.SimpleNamespace(returncode=1, stdout="", stderr="No such object")
        return types.SimpleNamespace(returncode=0, stdout="anvil-serving\n", stderr="")
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_up_delegates_to_compose_without_model_dependencies():
    calls = []
    assert router_manage.cmd_up("compose.yml", "router", _run=lambda argv, **kw: calls.append(argv) or _run(argv, **kw)) == 0
    assert calls[-1] == [
        "docker", "compose", "--project-name", "anvil-serving", "-f",
        "compose.yml", "up", "-d", "--no-deps", "router",
    ]


def test_up_recreate_forces_only_the_router_service():
    calls = []
    assert router_manage.cmd_up(
        "compose.yml",
        "router",
        recreate=True,
        _run=lambda argv, **kw: calls.append(argv) or _run(argv, **kw),
    ) == 0
    assert calls[-1] == [
        "docker", "compose", "--project-name", "anvil-serving", "-f",
        "compose.yml", "up", "-d", "--no-deps", "--force-recreate", "router",
    ]


def test_up_without_recreate_omits_force_recreate():
    assert "--force-recreate" not in router_manage._compose_up_argv("compose.yml", "router")


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


def test_down_uses_stable_anvil_serving_compose_project():
    calls = []

    assert router_manage.cmd_down(
        "compose.yml",
        "router",
        _run=lambda argv, **kwargs: calls.append(argv) or _run(argv, **kwargs),
    ) == 0
    assert calls == [[
        "docker", "compose", "--project-name", "anvil-serving",
        "-f", "compose.yml", "stop", "router",
    ]]


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


def test_explicit_compose_path_wins_over_operator_home_default(tmp_path, monkeypatch):
    operator_compose = tmp_path / "operator" / "docker-compose.yml"
    explicit_compose = tmp_path / "checked-in" / "docker-compose.yml"
    operator_compose.parent.mkdir()
    operator_compose.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(router_manage, "default_compose_candidates", lambda: [str(operator_compose)])

    assert router_manage.resolve_compose_path(str(explicit_compose)) == str(explicit_compose)


def test_status_reports_unavailable_docker(capsys):
    assert router_manage.cmd_status("router", _run=lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr="")) == 1
    assert "UNKNOWN" in capsys.readouterr().out


def test_up_refuses_foreign_compose_owner_without_recreate(capsys):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "State.Status" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(returncode=0, stdout="fakoli-dark\n", stderr="")
        return _run(argv, **kwargs)

    assert router_manage.cmd_up("compose.yml", "router", _run=run) == 1
    assert not any(argv[:2] == ["docker", "compose"] for argv in calls)
    assert "--recreate" in capsys.readouterr().err


def test_up_recreate_replaces_foreign_compose_owner():
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "State.Status" in " ".join(argv):
            return types.SimpleNamespace(returncode=0, stdout="running\n", stderr="")
        if argv[:2] == ["docker", "inspect"]:
            return types.SimpleNamespace(returncode=0, stdout="fakoli-dark\n", stderr="")
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
        ("quiesce", "fast-local"),
        ("drain", "fast-local"),
        ("status", None),
        ("status", None),
    ]
    assert installed == [str(config)]


def test_install_config_accepts_exact_desired_tiers_when_a_serve_is_unavailable(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text(
        """
[router]
[[router.tiers]]
id = "primary-local"
base_url = "http://127.0.0.1:30002/v1"
model = "primary"
dialect = "openai"
context_limit = 262144
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_LOCAL_KEY"
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
llm.primary = "primary-local"
llm.voice = "omni-local"
""",
        encoding="utf-8",
    )

    statuses = iter([
        {"tiers": [{"tier_id": "primary-local", "ready": True}]},
        {"tiers": [
            {"tier_id": "primary-local", "ready": True},
            {
                "tier_id": "omni-local",
                "ready": False,
                "readiness_state": "unavailable",
                "readiness_reason": "health_transport_URLError",
            },
        ]},
    ])

    def transition(action, **_kwargs):
        if action == "status":
            return next(statuses)
        if action == "drain":
            return {"result": {"drained": True}}
        return {"result": {"applied": True}}

    result = router_manage.install_config(
        str(config),
        confirm=True,
        dry_run=False,
        _transition=transition,
        _install=lambda _path: 0,
        _sleep=lambda _seconds: None,
    )

    assert result["applied"] is True
    assert result["unavailable_tiers"] == ["omni-local"]
    assert result["tier_status"][0]["tier_id"] == "primary-local"
    assert result["tier_status"][1]["readiness_reason"] == "health_transport_URLError"


def test_install_config_compensates_when_drain_fails(tmp_path):
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
        if action == "status":
            return {"tiers": [{"tier_id": "fast-local", "ready": True}]}
        if action == "drain":
            return {"result": {"drained": False}}
        return {"result": {"applied": True}}

    with pytest.raises(ValueError, match="did not drain"):
        router_manage.install_config(
            str(config),
            confirm=True,
            dry_run=False,
            _transition=transition,
            _install=lambda _path: pytest.fail("install ran after failed drain"),
        )

    assert calls[-1] == ("readmit", "fast-local")
