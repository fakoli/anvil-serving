from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving import cli
from anvil_serving import workbench


def test_workbench_up_dry_run_uses_packaged_compose_template(capsys):
    assert workbench.main(["up", "--dry-run"]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["dry_run"] is True
    assert rendered["command"][:3] == ["docker", "compose", "--project-name"]
    assert rendered["command"][-2:] == ["up", "--detach"]


@pytest.mark.parametrize("action", ("up", "down", "status", "logs"))
def test_workbench_missing_compose_file_is_a_clean_usage_error(action, tmp_path, capsys):
    assert workbench.main([action, "--compose", str(tmp_path / "missing.yml")]) == 2
    assert "does not exist" in json.loads(capsys.readouterr().err)["error"]


@pytest.mark.parametrize(
    ("argv", "suffix"),
    [
        (("up",), ("up", "--detach")),
        (("down",), ("down",)),
        (("status",), ("ps", "--format", "json")),
        (("logs", "--tail", "17"), ("logs", "--tail", "17")),
    ],
)
def test_workbench_commands_render_cross_platform_argument_arrays(argv, suffix):
    """Each lifecycle action uses a path-safe Docker argument array, not a shell string."""
    args = workbench._parser().parse_args(list(argv))
    command = workbench.compose_command(args)

    assert command[:3] == ["docker", "compose", "--project-name"]
    assert "-f" in command
    assert Path(command[command.index("-f") + 1]).is_file()
    assert tuple(command[-len(suffix):]) == suffix


def test_workbench_mutations_use_shared_confirmation_before_docker(capsys):
    for action in ("up", "down"):
        assert cli.main(["workbench", action]) == 3
        assert "rerun the same command with --confirm" in capsys.readouterr().err


def test_workbench_mutations_require_the_dispatcher_confirmation_scope(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        workbench.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(command) or type("Result", (), {"returncode": 0})(),
    )

    assert workbench.main(["up", "--confirm"]) == 3
    assert calls == []
    assert "confirmation required" in json.loads(capsys.readouterr().err)["error"]

    assert cli.main(["workbench", "up", "--confirm"]) == 0
    assert calls[0][-2:] == ["up", "--detach"]


@pytest.mark.parametrize("tail", ("0", "-1", "5001", "not-a-number"))
def test_workbench_logs_reject_unbounded_tails(tail):
    with pytest.raises(SystemExit) as exc:
        workbench._parser().parse_args(["logs", "--tail", tail])

    assert exc.value.code == 2


def test_workbench_dry_run_is_bounded_json_and_docs_describe_the_lifecycle(capsys):
    assert cli.main(["workbench", "up", "--dry-run"]) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["ok"] is True
    assert rendered["dry_run"] is True
    assert isinstance(rendered["command"], list)
    assert rendered["command"][-2:] == ["up", "--detach"]

    docs = (Path(__file__).resolve().parents[1] / "docs" / "WORKBENCH.md").read_text(encoding="utf-8")
    for action in ("up", "down", "status", "logs"):
        assert f"anvil-serving workbench {action}" in docs


def test_workbench_compose_waits_for_neo4j_and_ignores_its_secret_env_file():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "anvil_serving" / "_scaffold_templates" / "docker-compose.workbench.yml").read_text(encoding="utf-8")
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")

    assert "neo4j:\n        condition: service_healthy" in compose
    assert "cypher-shell" in compose
    assert "workbench.env" in gitignore
    assert "!workbench.env.example" in gitignore
