"""Hermetic contracts between the declarative CLI and leaf handlers."""

from __future__ import annotations

import io
import json

import pytest

from anvil_serving import cli
from anvil_serving import guard
from anvil_serving import router_manage
from anvil_serving.commands import COMMAND_TREE, CommandNode, HandlerRef


def _paths(
    nodes: tuple[CommandNode, ...] = COMMAND_TREE.nodes,
    prefix: tuple[CommandNode, ...] = (),
):
    for node in nodes:
        path = (*prefix, node)
        yield path
        yield from _paths(node.children, path)


def _leaf(path: tuple[str, ...]) -> CommandNode:
    nodes = COMMAND_TREE.nodes
    node = None
    for name in path:
        node = next(candidate for candidate in nodes if candidate.name == name)
        nodes = node.children
    assert node is not None
    return node


def _unbounded_json_cases() -> tuple[tuple[tuple[str, ...], str], ...]:
    cases: list[tuple[tuple[str, ...], str]] = []
    for path in _paths():
        command = tuple(node.name for node in path)
        node = path[-1]
        if node.visible and node.output_policy != "bounded":
            cases.append((command, node.output_policy))
        if not node.visible:
            continue
        for option in node.options:
            if option.output_policy is None:
                continue
            flag = next((item for item in option.flags if item.startswith("--")), option.flags[0])
            cases.append(((*command, flag), option.output_policy))
    return tuple(cases)


# Cover nesting depth and the protected families' operand-free JSON errors.
@pytest.mark.parametrize("path", [
    pytest.param(("router",), id="path4"),
])
def test_action_groups_reject_options_without_an_action(capsys, path):
    assert cli.main([*path, "--definitely-invalid"]) == 2
    human = capsys.readouterr()
    assert human.out == ""
    assert "action required before options" in human.err

    assert cli.main([*path, "--definitely-invalid", "--json"]) == 2
    machine = capsys.readouterr()
    assert machine.err == ""
    payload = json.loads(machine.out)
    assert payload["error"]["code"] == "missing_action"
    assert payload["error"]["details"]["actions"]
    if path[0] in {"router", "edge", "connect"}:
        assert "--definitely-invalid" not in machine.out
        assert payload["data"] is None


def test_mutating_commands_declare_confirmation_or_retain_handler_owned_policy():
    # Check the whole catalog without dispatching the same policy for every leaf.
    handler_owned = {
        " ".join(node.name for node in path)
        for path in _paths()
        if path[-1].handler is not None
        and path[-1].mutation_class == "mutate"
        and not any(
            "--confirm" in option.flags or option.requires_confirmation
            for option in path[-1].options
        )
    }
    assert handler_owned == {
        "init", "serves render", "router keys init", "router keys revoke",
        "router keys bind", "router keys backup", "router keys restore", "connect users",
    }


@pytest.mark.parametrize("path,forward_confirm", [
    (("models", "sync"), False),
    (("models", "cache", "remove"), True),
])
def test_explicit_confirmation_is_consumed_before_guarded_handler_dispatch(
    monkeypatch, path, forward_confirm
):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main([*path, "--confirm"]) == 0
    assert len(calls) == 1
    if forward_confirm:
        # Declared legacy handlers gate on their own argparse --confirm; the
        # dispatcher restores exactly one consumed token in argv.
        assert calls[0].count("--confirm") == 1
    else:
        assert "--confirm" not in calls[0]


def test_interactive_confirmation_dispatches_without_forwarding_policy_flag(monkeypatch):
    class InteractiveInput(io.StringIO):
        def isatty(self):
            return True

    calls: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main(["serves", "down", "heavy", "--manifest", "serves.toml"]) == 0
    assert calls == [["down", "heavy", "--manifest", "serves.toml"]]


def test_interactive_eof_fails_closed_without_dispatch(monkeypatch, capsys):
    class InteractiveInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("handler resolved after EOF"),
    )

    assert cli.main(["serves", "down"]) == 3
    assert "confirmation input unavailable" in capsys.readouterr().err


def test_dispatch_confirmation_authorizes_nested_guard_for_one_call(monkeypatch):
    calls = []

    def handler(argv):
        calls.append(argv)
        assert guard.confirm("nested", _input=lambda prompt: pytest.fail("nested prompt"))
        return 0

    monkeypatch.setattr(HandlerRef, "resolve", lambda self: handler)

    assert cli.main(["serves", "rm", "--confirm"]) == 0
    assert calls == [["rm"]]
    assert not guard.confirm("outside", _input=lambda prompt: "no")


def test_router_transition_receives_consumed_dispatch_confirmation(monkeypatch, capsys):
    seen = {}

    def transition_request(action, **kwargs):
        seen.update(action=action, **kwargs)
        return {"applied": True, "action": action}

    monkeypatch.setattr(router_manage, "transition_request", transition_request)

    assert (
        cli.main(
            [
                "router",
                "quiesce",
                "--tier",
                "primary-local",
                "--router-url",
                "http://127.0.0.1:18000",
                "--confirm",
            ]
        )
        == 0
    )
    assert seen["action"] == "quiesce"
    assert seen["confirm"] is True
    assert seen["dry_run"] is False
    assert json.loads(capsys.readouterr().out) == {
        "action": "quiesce",
        "applied": True,
    }


def test_confirmation_after_separator_is_a_leaf_argument_not_authorization(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("handler resolved without dispatcher confirmation"),
    )

    assert cli.main(["serves", "rm", "--", "--confirm"]) == 3
    assert "confirmation required" in capsys.readouterr().err


def test_confirmation_value_is_rejected_before_handler_resolution(monkeypatch, capsys):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("handler resolved after malformed confirmation"),
    )

    assert cli.main(["serves", "up", "--confirm=yes"]) == 2
    assert "--confirm does not accept a value" in capsys.readouterr().err


def test_eval_workload_requires_confirmation_and_strips_it_from_leaf(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: calls.append(argv) or 0)

    args = ["eval", "preflight", "--base-url", "http://127.0.0.1:8000/v1", "--model", "m"]
    assert cli.main(args) == 3
    assert "confirmation required" in capsys.readouterr().err
    assert calls == []

    assert cli.main([*args, "--confirm"]) == 0
    assert calls == [["--base-url", "http://127.0.0.1:8000/v1", "--model", "m"]]


def test_dry_run_does_not_require_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main(["serves", "up", "--dry-run"]) == 0
    assert calls == [["up", "--dry-run"]]


def test_separator_prevents_dispatcher_help_and_follow_policy(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main(["eval", "preflight", "--", "--help"]) == 0
    assert calls.pop() == ["--", "--help"]
    assert cli.main(["serves", "logs", "--json", "--", "--follow"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert calls == [["logs", "--", "--follow"]]


def test_json_token_reveal_requires_confirmation_before_handler(monkeypatch, capsys):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("resolved token handler without confirmation"),
    )

    assert cli.main(["router", "token", "--reveal", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "confirmation_required"
