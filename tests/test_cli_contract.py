"""Hermetic contracts between the declarative CLI and leaf handlers."""

from __future__ import annotations

import io
import json

import pytest

from anvil_serving import cli
from anvil_serving import guard
from anvil_serving.command_tree import COMMAND_TREE, CommandNode, HandlerRef
from anvil_serving.targets import ExecutionPlan


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


def _action_group_paths() -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(node.name for node in path)
        for path in _paths()
        if path[-1].visible
        and path[-1].children
        and path[-1].handler is None
        and path[-1].tombstone is None
    )


def _tombstone_cases() -> tuple[tuple[tuple[str, ...], str], ...]:
    cases: list[tuple[tuple[str, ...], str]] = []
    for path in _paths():
        command = tuple(node.name for node in path)
        node = path[-1]
        if node.tombstone is not None:
            cases.append((command, node.tombstone.replacement))
        for option in node.options:
            if option.tombstone is None:
                continue
            flag = next((item for item in option.flags if item.startswith("--")), option.flags[0])
            cases.append(((*command, flag), option.tombstone.replacement))
    return tuple(cases)


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


@pytest.mark.parametrize(("argv", "replacement"), _tombstone_cases())
def test_every_tombstone_refuses_human_and_json_before_resolution(
    monkeypatch, capsys, argv, replacement
):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved tombstone handler: {self.name}"),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_dispatch_plan",
        lambda *_args, **_kwargs: pytest.fail("resolved topology for tombstone"),
    )

    assert cli.main(list(argv)) == 2
    human = capsys.readouterr()
    assert human.out == ""
    assert replacement in human.err

    assert cli.main([*argv, "--json"]) == 2
    machine = capsys.readouterr()
    assert machine.err == ""
    payload = json.loads(machine.out)
    assert payload["error"]["class"] == "usage"
    assert payload["error"]["details"]["replacement"] == replacement


@pytest.mark.parametrize(("argv", "classification"), _unbounded_json_cases())
def test_every_unbounded_manifest_case_refuses_json_before_resolution(
    monkeypatch, capsys, argv, classification
):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved unbounded handler: {self.name}"),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_dispatch_plan",
        lambda *_args, **_kwargs: pytest.fail("resolved topology for unbounded JSON"),
    )

    assert cli.main([*argv, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"]["class"] == "usage"
    assert classification in payload["error"]["message"]


@pytest.mark.parametrize("path", _action_group_paths())
def test_every_action_group_rejects_options_without_an_action(capsys, path):
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


def test_eval_workload_requires_confirmation_and_strips_it_from_leaf(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: calls.append(argv) or 0)

    args = ["eval", "preflight", "--base-url", "http://127.0.0.1:8000/v1", "--model", "m"]
    assert cli.main(args) == 3
    assert "confirmation required" in capsys.readouterr().err
    assert calls == []

    assert cli.main([*args, "--confirm"]) == 0
    assert calls == [["--base-url", "http://127.0.0.1:8000/v1", "--model", "m"]]


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


def test_json_preserves_local_execution_context(monkeypatch, capsys):
    def local_plan(path, _options):
        return ExecutionPlan(
            command=cli._command_spec(path),
            topology_id="test-topology",
            topology_snapshot="sha256:test",
            command_host=None,
            command_runtime=None,
            execution_host=None,
            execution_runtime=None,
            resource_host=None,
            resource_runtime=None,
            resource=None,
            transport="local",
            transport_id=None,
            transport_endpoint=None,
            transport_host_key_fingerprint=None,
            transport_known_hosts_path=None,
            recovery_transport_id=None,
            recovery_transport_endpoint=None,
            recovery_host_key_fingerprint=None,
            recovery_known_hosts_path=None,
            resource_endpoint="http://127.0.0.1:9000",
            gpu_role=None,
            selected_target=None,
            capacity=guard.CapacityDecision(
                allowed=True,
                capacity_policy="test-policy",
                resource_workload="experimental-model",
                model_workload=True,
                experimental_model_workload_requested=True,
                experimental_model_workload_permitted=True,
                experimental_model_workload_override=True,
                warning="test capacity warning",
            ),
        )

    monkeypatch.setattr(cli, "_resolve_dispatch_plan", local_plan)
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: 0)

    assert cli.main(["controller", "status", "--topology", "test.toml", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context"]["topology"] == "test-topology"
    assert payload["context"]["transport"] == "local"
    assert payload["warnings"] == ["test capacity warning"]


def test_json_token_reveal_requires_confirmation_before_handler(monkeypatch, capsys):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("resolved token handler without confirmation"),
    )

    assert cli.main(["router", "token", "--reveal", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "confirmation_required"


def test_voice_profile_validation_requires_profile(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["voice", "profiles", "validate"])
    assert exc.value.code == 2
    assert "--profile" in capsys.readouterr().err
