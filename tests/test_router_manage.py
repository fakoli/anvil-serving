import json
import types

import pytest

from anvil_serving import router_manage


def _run(argv, **_kwargs):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_up_delegates_to_compose_without_model_dependencies():
    calls = []
    assert router_manage.cmd_up("compose.yml", "router", _run=lambda argv, **kw: calls.append(argv) or _run(argv, **kw)) == 0
    assert calls[0] == ["docker", "compose", "-f", "compose.yml", "up", "-d", "--no-deps", "router"]


def test_up_recreate_forces_only_the_router_service():
    calls = []
    assert router_manage.cmd_up(
        "compose.yml",
        "router",
        recreate=True,
        _run=lambda argv, **kw: calls.append(argv) or _run(argv, **kw),
    ) == 0
    assert calls[0] == [
        "docker", "compose", "-f", "compose.yml", "up", "-d", "--no-deps",
        "--force-recreate", "router",
    ]


def test_up_without_recreate_omits_force_recreate():
    assert "--force-recreate" not in router_manage._compose_up_argv("compose.yml", "router")


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
            "docker", "compose", "--env-file", str(env_file), "-f", str(compose), "up", "-d",
            "--no-deps", "--force-recreate", "router",
        ],
        "compose": str(compose),
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


def test_router_parser_does_not_offer_profile_promotion():
    parser = router_manage._build_parser()
    actions = next(item for item in parser._actions if item.dest == "action")
    assert "promote" not in actions.choices
