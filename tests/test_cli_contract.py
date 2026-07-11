"""Hermetic contracts between the declarative CLI and leaf handlers."""

from __future__ import annotations

import io

import pytest

from anvil_serving import cli
from anvil_serving import guard
from anvil_serving.command_tree import COMMAND_TREE, CommandNode, HandlerRef


def _paths(
    nodes: tuple[CommandNode, ...] = COMMAND_TREE.nodes,
    prefix: tuple[CommandNode, ...] = (),
):
    for node in nodes:
        path = (*prefix, node)
        yield path
        yield from _paths(node.children, path)


def _guarded_paths() -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(node.name for node in path)
        for path in _paths()
        if path[-1].handler is not None
        and path[-1].mutation_class == "mutate"
        and any("--confirm" in option.flags for option in path[-1].options)
    )


@pytest.mark.parametrize("path", _guarded_paths())
def test_explicit_confirmation_is_consumed_before_guarded_handler_dispatch(monkeypatch, path):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main([*path, "--confirm"]) == 0
    assert len(calls) == 1
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


def test_interactive_decline_fails_closed_without_dispatch(monkeypatch, capsys):
    class InteractiveInput(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(cli.sys, "stdin", InteractiveInput())
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("handler resolved after confirmation decline"),
    )

    assert cli.main(["serves", "down"]) == 3
    assert "confirmation declined" in capsys.readouterr().err


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


def test_confirmation_after_separator_is_a_leaf_argument_not_authorization(
    monkeypatch, capsys
):
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


@pytest.mark.parametrize(
    "argv",
    [
        ["serves", "up", "heavy", "--manifest", "serves.toml", "--confirm"],
        ["serves", "up", "--manifest", "serves.toml", "heavy", "--confirm"],
    ],
)
def test_leaf_positional_and_option_order_reaches_same_handler(monkeypatch, argv):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda forwarded: calls.append(forwarded) or 0,
    )

    assert cli.main(argv) == 0
    assert calls == [["up", *argv[2:-1]]]


@pytest.mark.parametrize(
    ("path", "flags"),
    [
        (["serves", "up"], {"--manifest", "--dry-run", "--compose", "--recreate"}),
        (["serves", "down"], {"--manifest", "--dry-run"}),
        (["serves", "rm"], {"--manifest", "--dry-run", "--yes"}),
        (["serves", "adopt"], {"--manifest", "--dry-run", "--yes"}),
    ],
)
def test_serves_focused_help_matches_leaf_action_flags(capsys, path, flags):
    assert cli.main([*path, "--help"]) == 0
    output = capsys.readouterr().out
    for flag in flags:
        assert flag in output


@pytest.mark.parametrize("action", ["add", "list", "render"])
def test_external_benchmark_notebook_actions_resolve(monkeypatch, action):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main(["eval", "benchmark", "external", "notebook", action]) == 0
    assert calls == [["notebook", action]]


def test_external_benchmark_notebook_help_lists_actions(capsys):
    assert cli.main(["eval", "benchmark", "external", "notebook", "--help"]) == 0
    output = capsys.readouterr().out
    for action in ("add", "list", "render"):
        assert action in output
