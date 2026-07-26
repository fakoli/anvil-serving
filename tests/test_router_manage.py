import types

from anvil_serving import router_manage


def _run(argv, **_kwargs):
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_up_delegates_to_compose_without_model_dependencies():
    calls = []
    assert router_manage.cmd_up("compose.yml", "router", _run=lambda argv, **kw: calls.append(argv) or _run(argv, **kw)) == 0
    assert calls[0] == ["docker", "compose", "-f", "compose.yml", "up", "-d", "--no-deps", "router"]


def test_status_reports_unavailable_docker(capsys):
    assert router_manage.cmd_status("router", _run=lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1, stdout="", stderr="")) == 1
    assert "UNKNOWN" in capsys.readouterr().out


def test_router_parser_does_not_offer_profile_promotion():
    parser = router_manage._build_parser()
    actions = next(item for item in parser._actions if item.dest == "action")
    assert "promote" not in actions.choices
