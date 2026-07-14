"""Tests for the anvil-serving CLI dispatch — in particular the early
Python-version guard (`anvil_serving.cli._check_python_version`) and the
`calibrate` verb (the operator entry to the guarded write-back batch, T006).
"""

import json
import re
import shlex
import socket
from pathlib import Path

import pytest

from anvil_serving import calibrate as calibrate_mod
from anvil_serving import cli
from anvil_serving import harness
from anvil_serving import host
from anvil_serving import benchmark, multiplexer, preflight
from anvil_serving import router_manage
from anvil_serving import serves
from anvil_serving.command_tree import COMMAND_TREE, CommandNode, CommandOption, HandlerRef


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _active_cli_document_paths():
    yield _REPO_ROOT / "README.md"
    for directory in ("docs", "examples"):
        for path in sorted((_REPO_ROOT / directory).rglob("*.md")):
            relative = path.relative_to(_REPO_ROOT)
            if "archive" not in relative.parts and "findings" not in relative.parts:
                yield path


def test_python_version_guard_blocks_old_interpreter():
    assert cli._check_python_version((3, 10, 0)) == (
        "anvil-serving needs Python >=3.11; you have 3.10"
    )


def test_python_version_guard_blocks_even_older_interpreter():
    assert cli._check_python_version((2, 7, 18)) == (
        "anvil-serving needs Python >=3.11; you have 2.7"
    )


def test_python_version_guard_allows_supported_interpreter():
    assert cli._check_python_version((3, 11, 0)) is None
    assert cli._check_python_version((3, 13, 0)) is None


def test_dashboard_serve_help_is_supported_and_read_only(capsys):
    rc = cli.main(["dashboard", "serve", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "packaged local dashboard" in out
    assert "--host" in out
    assert "--port" in out
    for forbidden in ("--start", "--stop", "--restart", "--configure"):
        assert forbidden not in out


def test_dashboard_serve_dispatch_does_not_duplicate_action(monkeypatch):
    from anvil_serving.observability.dashboard import app as dashboard_app

    seen = []
    monkeypatch.setattr(dashboard_app, "main", lambda argv: seen.append(argv) or 0)

    assert cli.main(["dashboard", "serve", "--port", "0"]) == 0
    assert seen == [["--port", "0"]]


def test_python_version_guard_blocks_main_under_simulated_old_interpreter(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "version_info", (3, 9, 0))
    rc = cli.main(["--help"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "anvil-serving needs Python >=3.11; you have 3.9" in captured.err


def test_top_level_help_groups_commands_and_shows_examples(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    for token in (
        "Data plane:",
        "Local serving tools:",
        "Quality loop:",
        "Control plane & integrations:",
        "Voice:",
        "Global options:",
        "--command-manifest",
        "anvil-serving --version",
        "router run",
        "eval preflight",
        "anvil-serving serves status",
        "http://127.0.0.1:30000/v1",
        "https://fakoli.github.io/anvil-serving/CLI/",
    ):
        assert token in out


def test_root_help_examples_execute_on_canonical_paths(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    examples = out.split("Examples:\n", 1)[1].split("\nDocs:", 1)[0]
    commands = [
        shlex.split(line.strip())[1:]
        for line in examples.splitlines()
        if line.startswith("  anvil-serving ")
    ]

    assert commands
    for command in commands:
        assert cli.main([*command, "--help"]) == 0
        assert "usage:" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_top_level_version_reports_installed_version(flag, capsys):
    rc = cli.main([flag])
    assert rc == 0
    assert capsys.readouterr().out == "anvil-serving %s\n" % cli.__version__


def test_command_manifest_is_terminal_and_machine_readable(capsys):
    assert cli.main(["--command-manifest"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert any(record["path"] == "topology resolve" for record in payload["commands"])

    assert cli.main(["--command-manifest", "router", "status"]) == 2
    assert "does not accept command arguments" in capsys.readouterr().err


def test_top_level_version_reads_installed_metadata(monkeypatch, capsys):
    monkeypatch.setattr(cli.importlib_metadata, "version", lambda name: "9.8.7+installed")
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out == "anvil-serving 9.8.7+installed\n"


def test_top_level_help_hides_compatibility_aliases(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    command_lines = [
        line.strip().split(None, 1)[0]
        for line in out.splitlines()
        if line.startswith("  ") and line.strip() and not line.strip().startswith("anvil-serving")
    ]
    for hidden in ("onboard", "voice-sidecar", "cache-prune", "score", "deploy", "external-bench"):
        assert hidden not in command_lines
    for visible in ("init", "voice", "models", "serves", "eval", "router"):
        assert visible in command_lines


def test_unknown_top_level_command_suggests_close_match(capsys):
    rc = cli.main(["routr"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown command: routr" in err
    assert "Did you mean 'router'?" in err
    assert "anvil-serving --help" in err


def test_unknown_command_suggests_canonical_replacement_for_hidden_alias(capsys):
    rc = cli.main(["deply"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Did you mean 'serves render'?" in err


def test_unknown_command_suggests_init_for_onboard_typo(capsys):
    rc = cli.main(["onboar"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Did you mean 'init'?" in err
    assert "Did you mean 'onboard'?" not in err


def test_unknown_nested_command_remains_a_refusal(capsys):
    assert cli.main(["eval", "benchmrk"]) == 2
    err = capsys.readouterr().err
    assert "unknown command: eval benchmrk" in err
    assert "Did you mean 'benchmark'?" in err
    assert "anvil-serving eval --help" in err


def test_unknown_external_action_points_to_external_parser_help(capsys):
    assert cli.main(["eval", "benchmark", "external", "frobnicate"]) == 2
    err = capsys.readouterr().err
    assert "unknown command: eval benchmark external frobnicate" in err
    assert "anvil-serving eval benchmark external --help" in err
    assert "anvil-serving --help" not in err


def test_removed_path_refuses_without_resolving_a_legacy_tail(capsys):
    assert cli.main(["external-bench", "lissst"]) == 2
    err = capsys.readouterr().err
    assert "`external-bench` was removed" in err
    assert "`eval benchmark external`" in err


@pytest.mark.parametrize(
    ("argv", "replacement"),
    [
        (["onboard", "--help"], "init"),
        (["voice-sidecar", "--help"], "voice sidecar"),
        (["cache-prune", "--help"], "models cache prune"),
        (["score", "--help"], "models score"),
        (["deploy", "--help"], "serves render"),
        (["external-bench", "--help"], "eval benchmark external"),
    ],
)
def test_removed_root_paths_emit_only_migration_guidance(argv, replacement, capsys):
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "was removed" in captured.err
    assert f"`{replacement}`" in captured.err
    assert "docs/CLI.md#migration-from-legacy-commands" in captured.err


@pytest.mark.parametrize(
    ("argv", "replacement"),
    [
        (["serve"], "router run"),
        (["deploy"], "serves render"),
        (["multiplexer"], "serves multiplex"),
        (["cache-prune"], "models cache prune"),
        (["score"], "models score"),
        (["profile"], "eval usage"),
        (["preflight"], "eval preflight"),
        (["benchmark"], "eval benchmark capacity"),
        (["external-bench"], "eval benchmark external"),
        (["calibrate"], "eval calibrate"),
        (["gpus"], "host gpus"),
        (["models", "recipe", "list"], "models recipes list"),
        (["models", "recipe", "show"], "models recipes show"),
        (["voice-sidecar"], "voice sidecar"),
        (["voice", "up"], "voice audio up"),
        (["voice", "down"], "voice audio down"),
        (["voice", "run"], "voice proxy run"),
        (["voice", "bridge"], "voice proxy bridge"),
        (["voice", "start"], "voice audio up"),
        (["voice", "stop"], "voice audio down"),
        (["onboard"], "init"),
        (["mcp"], "mcp serve"),
        (["mcp", "list-tools"], "mcp tools"),
        (["mcp", "--list-tools"], "mcp tools"),
        (
            ["controller", "serve", "--allow-unauthenticated-loopback"],
            "Configure the token named by --auth-token-env",
        ),
    ],
)
def test_removed_forms_refuse_without_resolving_a_handler(monkeypatch, capsys, argv, replacement):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved removed path handler: {self.name}"),
    )

    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert replacement in captured.err
    assert "docs/CLI.md#migration-from-legacy-commands" in captured.err


def test_mcp_canonical_paths_dispatch_while_removed_forms_refuse(monkeypatch, capsys):
    from anvil_serving import mcp

    calls = []
    monkeypatch.setattr(mcp, "main", lambda argv: calls.append(argv) or 0)

    assert cli.main(["mcp", "tools"]) == 0
    assert cli.main(["mcp", "serve"]) == 0
    assert cli.main(["mcp"]) == 2
    assert cli.main(["mcp", "--list-tools"]) == 2
    assert cli.main(["mcp", "list-tools"]) == 2
    captured = capsys.readouterr()
    assert calls == [["list-tools"], []]
    assert captured.out == ""
    assert "`mcp` was removed; use `mcp serve`" in captured.err
    assert "`mcp --list-tools` was removed; use `mcp tools`" in captured.err
    assert "`mcp list-tools` was removed; use `mcp tools`" in captured.err


def test_removed_path_json_emits_one_structured_error_envelope(capsys):
    assert cli.main(["deploy", "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert set(payload) == {"ok", "command", "context", "data", "warnings", "error"}
    assert payload["error"]["class"] == "usage"
    assert payload["error"]["details"]["replacement"] == "serves render"
    assert (
        payload["error"]["details"]["docs_anchor"] == "docs/CLI.md#migration-from-legacy-commands"
    )


def test_global_json_wraps_root_and_nested_dispatch(capsys):
    assert cli.main(["--json", "--help"]) == 0
    root = json.loads(capsys.readouterr().out)
    assert root["ok"] is True
    assert "anvil-serving - quality-gated" in root["data"]

    assert cli.main(["mcp", "tools", "--json"]) == 0
    nested = json.loads(capsys.readouterr().out)
    assert nested["ok"] is True
    assert "router_status" in nested["data"]


def test_incompatible_global_verbosity_exits_usage_without_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: pytest.fail("handler resolved"))
    assert cli.main(["controller", "status", "--quiet", "--verbose"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot be used together" in captured.err


def test_incompatible_json_globals_emit_only_usage_envelope(capsys):
    assert cli.main(["--json", "--quiet", "--verbose", "controller", "status"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["error"]["class"] == "usage"


def test_json_mutation_never_prompts_and_requires_confirmation(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("prompted in JSON mode"))
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: pytest.fail("handler resolved"))
    assert cli.main(["router", "up", "--json"]) == 3
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["error"]["class"] == "safety"
    assert "--confirm" in payload["error"]["message"]


@pytest.mark.parametrize("policy", ["foreground", "protocol"])
def test_declarative_command_policy_classifies_synthetic_commands(policy):
    node = CommandNode("synthetic", "Synthetic command.", output_policy=policy)
    assert cli.command_policy((node,), ()).classification == policy


def test_declarative_command_policy_classifies_active_follow_option():
    follow = CommandOption(("--follow",), "Follow output.", output_policy="follow")
    node = CommandNode("synthetic", "Synthetic command.", options=(follow,))
    assert cli.command_policy((node,), ("--follow",)).classification == "follow"


@pytest.mark.parametrize(
    ("argv", "classification"),
    [
        (["router", "run", "--json"], "foreground"),
        (["serves", "multiplex", "--json"], "foreground"),
        (["voice", "proxy", "run", "--json"], "foreground"),
        (["controller", "serve", "--json"], "foreground"),
        (["mcp", "serve", "--json"], "protocol"),
        (["router", "logs", "--follow", "--json"], "follow"),
        (["serves", "logs", "--json", "--follow"], "follow"),
        (["host", "reclaim", "--json", "--confirm", "--watch", "--threshold-gb", "40"], "follow"),
    ],
)
def test_real_unbounded_commands_refuse_json_before_handler_resolution(
    monkeypatch, capsys, argv, classification
):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved unbounded handler: {self.name}"),
    )

    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["error"]["class"] == "usage"
    assert classification in payload["error"]["message"]


def test_mcp_serve_json_refusal_happens_before_protocol_handler_startup(monkeypatch, capsys):
    from anvil_serving import mcp

    monkeypatch.setattr(
        mcp,
        "main",
        lambda _argv: pytest.fail("mcp protocol handler started for --json"),
    )

    assert cli.main(["mcp", "serve", "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["command"] == "mcp serve"
    assert payload["error"]["class"] == "usage"
    assert "protocol command output" in payload["error"]["message"]


def test_bounded_logs_json_still_dispatches(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    assert cli.main(["router", "logs", "--tail", "5", "--json"]) == 0
    assert calls == [["logs", "--tail", "5"]]
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--experimental-model-workload=x", "controller", "status"],
        ["controller", "status", "--experimental-model-workload=x"],
    ],
)
def test_malformed_experimental_override_is_order_independent_and_pre_dispatch(
    monkeypatch, capsys, argv
):
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved malformed-option handler: {self.name}"),
    )

    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--experimental-model-workload does not accept a value" in captured.err


def _write_capacity_topology(
    tmp_path: Path,
    *,
    owner: str = "mini",
    workload: str = "experimental-model",
    allow_model_workloads: bool = False,
    allow_experimental_model_workloads: bool = False,
) -> Path:
    topology = tmp_path / f"{owner}-{workload}.toml"
    topology.write_text(
        f"""\
schema_version = 1
id = "synthetic-cli-capacity"
command_host = "host:operator"
command_runtime = "runtime:operator-native"

[[capacity_policies]]
id = "owner-capacity"
allow_model_workloads = {str(allow_model_workloads).lower()}
allow_experimental_model_workloads = {str(allow_experimental_model_workloads).lower()}

[[hosts]]
id = "operator"
roles = ["operator"]
address = "127.0.0.1"

[[hosts]]
id = "{owner}"
roles = ["controller"]
address = "192.0.2.20"
capacity_policy = "owner-capacity"

[[runtimes]]
id = "operator-native"
host = "operator"
role = "native"

[[runtimes]]
id = "owner-native"
host = "{owner}"
role = "native"

[[resources]]
id = "controller-service"
role = "controller"
host = "{owner}"
runtime = "owner-native"
workload = "{workload}"

[[transports]]
id = "owner-controller"
kind = "controller"
host = "{owner}"
runtime = "owner-native"
endpoint = "http://192.0.2.20:8766"
auth_env = "ANVIL_CONTROLLER_TOKEN"
allowed_operations = ["controller-status"]
""",
        encoding="utf-8",
    )
    return topology


def _write_remote_router_topology(tmp_path: Path, operation: str) -> Path:
    topology = tmp_path / f"router-{operation}.toml"
    topology.write_text(
        f"""\
schema_version = 1
id = "synthetic-router-cli"
command_host = "host:operator"
command_runtime = "runtime:operator-native"

[[hosts]]
id = "operator"
roles = ["operator"]
address = "127.0.0.1"
os = "linux"

[[hosts]]
id = "dark"
roles = ["router"]
address = "100.87.34.66"
os = "windows"

[[runtimes]]
id = "operator-native"
host = "operator"
role = "native"

[[runtimes]]
id = "dark-native"
host = "dark"
role = "native"

[[resources]]
id = "router-service"
role = "router"
host = "dark"
runtime = "dark-native"
endpoint = "http://127.0.0.1:8000"
endpoint_kind = "http"

[[transports]]
id = "dark-controller"
kind = "controller"
host = "dark"
runtime = "dark-native"
endpoint = "http://100.87.34.66:8765"
auth_env = "ANVIL_CONTROLLER_TOKEN"
allowed_operations = ["{operation}"]
""",
        encoding="utf-8",
    )
    return topology


def test_cli_remote_router_restart_dispatches_typed_operation(tmp_path, monkeypatch, capsys):
    topology = _write_remote_router_topology(tmp_path, "router-restart")
    seen = {}

    class FakeController:
        def __init__(self, endpoint, **kwargs):
            seen["controller"] = (endpoint, kwargs)

    def fake_execute(plan, operation, **kwargs):
        seen["plan"] = plan
        seen["operation"] = operation
        seen["execute_kwargs"] = kwargs
        return cli.TransportResult(operation.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "ControllerTransport", FakeController)
    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote router dispatch imported the local handler"),
    )

    assert (
        cli.main(
            [
                "router",
                "restart",
                "--topology",
                str(topology),
                "--confirm",
                "--container",
                "router-prod",
                "--no-verify",
            ]
        )
        == 0
    )
    operation = seen["operation"]
    assert operation.name == "router-restart"
    assert operation.tool_name == "router_manage"
    assert dict(operation.arguments) == {
        "action": "restart",
        "container": "router-prod",
        "no_verify": True,
        "confirm": True,
        "dry_run": False,
    }
    assert seen["controller"] == (
        "http://100.87.34.66:8765",
        {
            "auth_env": "ANVIL_CONTROLLER_TOKEN",
            "allowed_operations": ("router-restart",),
            "timeout_seconds": 60.0,
        },
    )
    assert seen["execute_kwargs"]["idempotency_key"].startswith("cli-")
    assert "transport=controller" in capsys.readouterr().out


def test_cli_remote_router_rejects_untyped_arguments_before_transport(
    tmp_path, monkeypatch, capsys
):
    topology = _write_remote_router_topology(tmp_path, "router-status")
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda *_args, **_kwargs: pytest.fail("invalid arguments reached transport"),
    )

    assert (
        cli.main(
            [
                "router",
                "status",
                "--topology",
                str(topology),
                "--shell",
                "whoami",
            ]
        )
        == 2
    )
    assert "not supported for remote status" in capsys.readouterr().err


def test_cli_remote_router_dry_run_never_generates_mutation_idempotency(tmp_path, monkeypatch):
    topology = _write_remote_router_topology(tmp_path, "router-restart")
    seen = {}

    def fake_execute(plan, operation, **kwargs):
        seen["arguments"] = dict(operation.arguments)
        seen["idempotency_key"] = kwargs["idempotency_key"]
        return cli.TransportResult(operation.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "execute_plan", fake_execute)

    assert (
        cli.main(
            [
                "router",
                "restart",
                "--topology",
                str(topology),
                "--dry-run",
            ]
        )
        == 0
    )
    assert seen == {
        "arguments": {"action": "restart", "dry_run": True},
        "idempotency_key": None,
    }


def test_cli_remote_router_reconciles_ambiguous_confirmed_mutation(tmp_path, monkeypatch, capsys):
    topology = _write_remote_router_topology(tmp_path, "router-restart")
    seen = {}

    class FakeController:
        def __init__(self, *_args, **_kwargs):
            pass

        def operation_status(self, key):
            seen["status_key"] = key
            return cli.TransportResult(
                "operation-status",
                "controller",
                {"status": "succeeded", "response": {"ok": True}},
            )

    def ambiguous(*_args, **kwargs):
        seen["dispatch_key"] = kwargs["idempotency_key"]
        raise cli.AdapterTransportError(
            "controller_timeout",
            "response was lost after dispatch",
            execution_state="partial_result",
        )

    monkeypatch.setattr(cli, "ControllerTransport", FakeController)
    monkeypatch.setattr(cli, "execute_plan", ambiguous)

    assert (
        cli.main(
            [
                "router",
                "restart",
                "--topology",
                str(topology),
                "--confirm",
            ]
        )
        == 0
    )
    assert seen["dispatch_key"] == seen["status_key"]
    assert seen["status_key"].startswith("cli-")
    assert "operation-status" in capsys.readouterr().out


def test_cli_remote_router_json_preserves_structured_result_and_context(
    tmp_path, monkeypatch, capsys
):
    topology = _write_remote_router_topology(tmp_path, "router-status")

    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda plan, operation, **_kwargs: cli.TransportResult(
            operation.name,
            "controller",
            {"ok": True, "data": {"running": True}},
        ),
    )

    assert (
        cli.main(
            [
                "--json",
                "router",
                "status",
                "--topology",
                str(topology),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["context"]["execution_host"] == "dark"
    assert payload["context"]["transport"] == "controller"
    assert payload["data"]["operation"] == "router-status"
    assert payload["data"]["data"]["data"]["running"] is True


def test_cli_remote_eval_dispatches_confirmed_typed_probe(tmp_path, monkeypatch):
    topology = _write_remote_router_topology(tmp_path, "eval-preflight")
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["evaluation"]')
    text = text.replace('role = "router"', 'role = "evaluation"')
    topology.write_text(text, encoding="utf-8")
    seen = {}

    def fake_execute(plan, operation, **kwargs):
        seen["operation"] = operation
        seen["key"] = kwargs["idempotency_key"]
        return cli.TransportResult(operation.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote eval imported the local handler"),
    )

    assert (
        cli.main(
            [
                "eval",
                "preflight",
                "--topology",
                str(topology),
                "--confirm",
                "--base-url",
                "http://127.0.0.1:8000/v1",
                "--model",
                "served",
                "--needle-ctx",
                "4096",
                "--timeout-seconds",
                "60",
            ]
        )
        == 0
    )
    assert seen["operation"].name == "eval-preflight"
    assert seen["operation"].tool_name == "preflight_probe"
    assert dict(seen["operation"].arguments) == {
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "served",
        "needle_ctx": 4096,
        "timeout_seconds": 60,
        "dry_run": False,
        "confirm": True,
    }
    assert seen["key"].startswith("cli-")


def test_remote_transport_timeout_covers_declared_workload_deadline():
    assert cli._remote_transport_timeout({}) == 60.0
    assert cli._remote_transport_timeout({"timeout_seconds": 300}) == 305.0
    assert cli._remote_transport_timeout({"timeout_seconds": 7200}) == 7205.0
    assert cli._remote_transport_timeout(
        {"timeout_seconds": 30, "checks": "smoke,json"},
        tool_name="preflight_probe",
    ) == 65.0
    assert cli._remote_transport_timeout(
        {"timeout_seconds": 30}, tool_name="preflight_probe"
    ) == 125.0
    assert cli._remote_transport_timeout(
        {"timeout_seconds": 3600, "checks": "smoke", "dry_run": True},
        tool_name="preflight_probe",
    ) == 60.0
    with pytest.raises(cli.TransportError, match="remote workload deadline exceeds"):
        cli._remote_transport_timeout(
            {"timeout_seconds": 3600, "checks": "smoke,json,needle,tools"},
            tool_name="preflight_probe",
        )


def test_cli_remote_eval_rejects_operator_manifest_before_transport(tmp_path, monkeypatch, capsys):
    topology = _write_remote_router_topology(tmp_path, "eval-preflight")
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["evaluation"]')
    text = text.replace('role = "router"', 'role = "evaluation"')
    topology.write_text(text, encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda *_args, **_kwargs: pytest.fail("manifest argument reached transport"),
    )

    assert (
        cli.main(
            [
                "eval",
                "preflight",
                "--topology",
                str(topology),
                "--confirm",
                "--manifest",
                "serves.toml",
                "--tier",
                "fast",
            ]
        )
        == 2
    )
    assert "not supported for remote preflight" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("path", "operation", "tool", "arguments"),
    [
        (
            ["harness", "sync", "openclaw"],
            "harness-sync-openclaw",
            "openclaw_sync",
            {"config": "router.toml", "out": "openclaw.json", "confirm": True, "dry_run": False},
        ),
        (
            ["harness", "restart", "openclaw"],
            "harness-restart-openclaw",
            "openclaw_gateway_restart",
            {"confirm": True, "dry_run": False},
        ),
        (
            ["harness", "status", "openclaw"],
            "harness-status-openclaw",
            "openclaw_gateway_status",
            {"timeout_seconds": 7},
        ),
    ],
)
def test_cli_remote_harness_operations_are_typed_and_controller_first(
    tmp_path, monkeypatch, path, operation, tool, arguments
):
    topology = _write_remote_router_topology(tmp_path, operation)
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["gateway"]')
    text = text.replace('role = "router"', 'role = "gateway"')
    topology.write_text(text, encoding="utf-8")
    seen = {}

    def fake_execute(plan, dispatched, **kwargs):
        seen["plan"] = plan
        seen["operation"] = dispatched
        seen["kwargs"] = kwargs
        return cli.TransportResult(dispatched.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        cli,
        "SSHRecoveryTransport",
        lambda *_args, **_kwargs: pytest.fail("normal auto mode constructed SSH recovery"),
    )
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote harness imported its local/SSH handler"),
    )
    leaf_args = {
        "openclaw_sync": ["--confirm", "--config", "router.toml", "--out", "openclaw.json"],
        "openclaw_gateway_restart": ["--confirm"],
        "openclaw_gateway_status": ["--timeout-seconds", "7"],
    }[tool]
    assert cli.main([*path, "--topology", str(topology), *leaf_args]) == 0
    assert seen["plan"].transport == "controller"
    assert seen["operation"].name == operation
    assert seen["operation"].tool_name == tool
    assert dict(seen["operation"].arguments) == arguments


def _append_harness_ssh_transport(topology, tmp_path, operation):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("synthetic\n", encoding="utf-8")
    known_hosts_toml = str(known_hosts).replace("\\", "\\\\")
    with topology.open("a", encoding="utf-8") as handle:
        handle.write(f'''\n[[transports]]
id = "gateway-ssh-recovery"
kind = "ssh"
host = "dark"
runtime = "dark-native"
endpoint = "ssh://operator@100.87.34.66:22"
allowed_operations = ["{operation}"]
host_key_fingerprint = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
known_hosts_path = "{known_hosts_toml}"
''')
    return known_hosts


def test_cli_harness_restart_ssh_fallback_is_explicit_and_fixed(tmp_path, monkeypatch, capsys):
    operation = "harness-restart-openclaw"
    topology = _write_remote_router_topology(tmp_path, operation)
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["gateway"]')
    text = text.replace('role = "router"', 'role = "gateway"')
    topology.write_text(text, encoding="utf-8")
    known_hosts = _append_harness_ssh_transport(topology, tmp_path, operation)
    identity = tmp_path / "id_recovery"
    identity.write_text("synthetic", encoding="utf-8")
    monkeypatch.setenv("ANVIL_SSH_IDENTITY_FILE", str(identity))
    seen = {}

    class FakeController:
        def __init__(self, endpoint, **_kwargs):
            self.endpoint = endpoint

        def execute(self, *_args, **_kwargs):
            raise cli.AdapterTransportError("controller_connect_failed", "refused before dispatch")

    class FakeSSH:
        def __init__(self, endpoint, **kwargs):
            self.endpoint = endpoint
            self.host = "100.87.34.66"
            self.transport_id = kwargs["transport_id"]
            self.known_hosts_path = str(known_hosts)
            self.host_key_fingerprint = kwargs["host_key_fingerprint"]
            seen["adapters"] = kwargs["adapters"]

        def execute(self, dispatched):
            seen["operation"] = dispatched
            return cli.TransportResult(dispatched.name, "ssh", {"returncode": 0})

    monkeypatch.setattr(cli, "ControllerTransport", FakeController)
    monkeypatch.setattr(cli, "SSHRecoveryTransport", FakeSSH)

    assert (
        cli.main(
            [
                "harness",
                "restart",
                "openclaw",
                "--topology",
                str(topology),
                "--allow-ssh-fallback",
                "--confirm",
            ]
        )
        == 0
    )
    assert seen["adapters"] == {
        operation: ("anvil-serving", "harness", "restart", "openclaw", "--confirm")
    }
    assert seen["operation"].name == operation
    assert dict(seen["operation"].arguments) == {}
    output = capsys.readouterr().out
    assert "transport=ssh" in output
    assert "controller=http://100.87.34.66:8765" in output


@pytest.mark.parametrize("leading", [False, True])
def test_cli_rejects_ssh_fallback_for_non_recovery_operation(tmp_path, capsys, leading):
    topology = _write_remote_router_topology(tmp_path, "router-status")
    argv = ["router", "status", "--topology", str(topology)]
    argv = ["--allow-ssh-fallback", *argv] if leading else [*argv, "--allow-ssh-fallback"]
    assert cli.main(argv) == 2
    assert "not recovery-capable" in capsys.readouterr().err


def test_topology_resolve_json_is_structured_and_contextual(capsys):
    topology = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"
    assert (
        cli.main(
            [
                "--json",
                "topology",
                "resolve",
                "--topology",
                str(topology),
                "--command",
                "host status",
                "--target",
                "host:fakoli-mini",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["context"]["execution_host"] == "fakoli-mini"
    assert payload["context"]["resource"] == "mini-host"
    assert payload["data"]["resolved_command"] == "host-status"
    assert not isinstance(payload["data"], str)


def test_voice_audio_dispatch_resolves_coowned_dark_resources_and_forwards_context(
    monkeypatch, capsys
):
    topology = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"
    seen = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: seen.append(argv) or 0,
    )

    assert (
        cli.main(
            [
                "voice",
                "audio",
                "up",
                "--topology",
                str(topology),
                "--command-host",
                "host:fakoli-dark",
                "--command-runtime",
                "runtime:dark-docker",
                "--dry-run",
            ]
        )
        == 0
    )

    assert seen == [
        [
            "audio",
            "up",
            "--dry-run",
            "--topology",
            str(topology),
            "--command-host",
            "host:fakoli-dark",
            "--command-runtime",
            "runtime:dark-docker",
            "--transport",
            "auto",
        ]
    ]
    assert capsys.readouterr().err == ""


def test_voice_benchmark_json_includes_resolved_proxy_context(monkeypatch, capsys):
    topology = Path(__file__).parent.parent / "examples" / "fakoli-dark" / "operator-topology.toml"
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: 0)

    assert (
        cli.main(
            [
                "voice",
                "benchmark",
                "--topology",
                str(topology),
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["context"]["resource"] == "mini-realtime-proxy"
    assert payload["context"]["execution_host"] == "fakoli-mini"
    assert payload["context"]["resource_endpoint"] == "http://127.0.0.1:8765/usage"


@pytest.mark.parametrize("path", [["voice", "profiles"], ["voice", "sidecar"]])
def test_offline_voice_group_help_hides_target_resolution(path, capsys):
    assert cli.main([*path, "--help"]) == 0
    output = capsys.readouterr().out
    assert "--topology" not in output
    assert "--transport" not in output


def test_cli_remote_host_repair_is_typed_and_os_checked(tmp_path, monkeypatch, capsys):
    topology = _write_remote_router_topology(tmp_path, "host-wsl-config")
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["host"]')
    text = text.replace('role = "router"', 'role = "host"')
    topology.write_text(text, encoding="utf-8")
    seen = {}

    def fake_execute(plan, operation, **kwargs):
        seen["plan"] = plan
        seen["operation"] = operation
        seen["key"] = kwargs["idempotency_key"]
        return cli.TransportResult(operation.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote host repair imported the local handler"),
    )
    assert (
        cli.main(["host", "wsl-config", "--topology", str(topology), "--confirm", "--memory", "80"])
        == 0
    )
    assert seen["plan"].execution_host.os == "windows"
    assert seen["operation"].tool_name == "host_manage"
    assert dict(seen["operation"].arguments) == {
        "action": "wsl-config",
        "memory": 80,
        "confirm": True,
        "dry_run": False,
    }
    assert seen["key"].startswith("cli-")

    text = topology.read_text(encoding="utf-8").replace('os = "windows"', 'os = "macos"')
    topology.write_text(text, encoding="utf-8")
    assert (
        cli.main(["host", "wsl-config", "--topology", str(topology), "--confirm", "--memory", "80"])
        == 3
    )
    assert "does not support host OS" in capsys.readouterr().err


def test_cli_explicit_ssh_restart_dry_run_needs_no_identity_or_process(
    tmp_path, monkeypatch, capsys
):
    operation = "harness-restart-openclaw"
    topology = _write_remote_router_topology(tmp_path, operation)
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["gateway"]')
    text = text.replace('role = "router"', 'role = "gateway"')
    topology.write_text(text, encoding="utf-8")
    _append_harness_ssh_transport(topology, tmp_path, operation)
    monkeypatch.delenv("ANVIL_SSH_IDENTITY_FILE", raising=False)
    monkeypatch.setattr(
        cli,
        "SSHRecoveryTransport",
        lambda *_args, **_kwargs: pytest.fail("SSH dry-run constructed a process adapter"),
    )

    assert (
        cli.main(
            [
                "harness",
                "restart",
                "openclaw",
                "--topology",
                str(topology),
                "--transport",
                "ssh",
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "transport=ssh" in output
    assert '"dry_run": true' in output


@pytest.mark.parametrize("experimental_flag", [False, True])
def test_cli_rejects_mini_model_workload_without_topology_permission_before_launch(
    tmp_path, monkeypatch, capsys, experimental_flag
):
    topology = _write_capacity_topology(tmp_path, workload="llm")
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved handler after capacity refusal: {self.name}"),
    )
    argv = ["controller", "status", "--topology", str(topology)]
    if experimental_flag:
        argv.append("--experimental-model-workload")

    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "capacity policy" in captured.err


def test_cli_rejects_topology_only_mini_override_before_launch(tmp_path, monkeypatch, capsys):
    topology = _write_capacity_topology(tmp_path, allow_experimental_model_workloads=True)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved handler after capacity refusal: {self.name}"),
    )

    assert cli.main(["controller", "status", "--topology", str(topology)]) == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "pass --experimental-model-workload" in captured.err


def test_cli_allows_capacity_override_and_probes_resolved_controller(tmp_path, monkeypatch, capsys):
    topology = _write_capacity_topology(tmp_path, allow_experimental_model_workloads=True)
    seen = []
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: seen.append(argv) or 0)

    assert (
        cli.main(
            [
                "controller",
                "status",
                "--topology",
                str(topology),
                "--experimental-model-workload",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "transport=controller" in captured.out
    assert seen == [
        [
            "status",
            "--url",
            "http://192.0.2.20:8766",
            "--auth-token-env",
            "ANVIL_CONTROLLER_TOKEN",
        ]
    ]


def test_cli_remote_dark_owner_probes_resolved_controller(tmp_path, monkeypatch, capsys):
    topology = _write_capacity_topology(
        tmp_path,
        owner="dark",
        workload="llm",
        allow_model_workloads=True,
    )
    seen = []
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: lambda argv: seen.append(argv) or 0)

    assert cli.main(["controller", "status", "--topology", str(topology)]) == 0
    captured = capsys.readouterr()
    assert "execution=dark" in captured.out
    assert seen[0][-4:] == [
        "--url",
        "http://192.0.2.20:8766",
        "--auth-token-env",
        "ANVIL_CONTROLLER_TOKEN",
    ]


def test_experimental_override_cannot_make_a_removed_path_callable(tmp_path, monkeypatch, capsys):
    topology = _write_capacity_topology(tmp_path, allow_experimental_model_workloads=True)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail(f"resolved removed path handler: {self.name}"),
    )

    assert (
        cli.main(
            [
                "serve",
                "--topology",
                str(topology),
                "--experimental-model-workload",
            ]
        )
        == 2
    )
    assert "`serve` was removed" in capsys.readouterr().err


def test_focused_action_help_for_operational_verbs(capsys):
    with pytest.raises(SystemExit) as exc:
        router_manage.main(["logs", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving router logs" in out
    assert "--tail" in out

    with pytest.raises(SystemExit) as exc:
        serves.main(["logs", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving serves logs" in out
    assert "--follow" in out

    with pytest.raises(SystemExit) as exc:
        host.main(["wsl-config", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving host wsl-config" in out
    assert "--memory" in out

    with pytest.raises(SystemExit) as exc:
        preflight.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving eval preflight" in out
    assert "direct endpoint input" in out
    assert "serves manifest input" in out
    assert "--base-url" in out and "--manifest" in out and "--tier" in out

    with pytest.raises(SystemExit) as exc:
        multiplexer.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving serves multiplex" in out
    assert "--ram-cap-gb" in out

    with pytest.raises(SystemExit) as exc:
        harness.main(["restart", "openclaw", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: anvil-serving harness restart openclaw" in out
    assert "--timeout-seconds" in out

    for workload in ("capacity", "quality"):
        with pytest.raises(SystemExit) as exc:
            benchmark.main([workload, "--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "usage: anvil-serving eval benchmark %s" % workload in out
        assert "direct endpoint input" in out
        assert "serves manifest input" in out
        assert "--base-url" in out and "--manifest" in out and "--tier" in out
        assert "--timeout-seconds" in out


def test_serves_help_explains_each_action(capsys):
    with pytest.raises(SystemExit) as exc:
        serves.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in (
        "Show docker and health state",
        "verify they stay stopped",
        "explicit confirmation",
        "externally-started serves",
        "streaming docker logs",
        "Render tuned compose",
    ):
        assert token in out


def test_focused_action_help_includes_action_specific_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        router_manage.main(["promote", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in (
        "--profile",
        "--config",
        "--container",
        "--cfg-volume",
        "--image",
        "--profile-dest",
        "--config-dest",
        "--no-reload",
    ):
        assert token in out

    with pytest.raises(SystemExit) as exc:
        harness.main(["sync", "openclaw", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in (
        "--config",
        "--out",
        "--base-url",
        "--api-key-env",
        "--gateway-host",
        "--gateway-path",
        "--overwrite",
        "--restart",
        "--skills",
        "--skill-dir",
        "--voice",
        "--voice-consult-model",
        "--voice-consult-thinking-level",
    ):
        assert token in out

    with pytest.raises(SystemExit) as exc:
        serves.main(["up", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in ("--manifest", "--dry-run", "--recreate", "--compose"):
        assert token in out


def _visible_paths(nodes=COMMAND_TREE.nodes, prefix=()):
    for node in nodes:
        path = prefix + (node.name,)
        if node.visible:
            yield path
        yield from _visible_paths(node.children, path)


@pytest.mark.parametrize("command", list(_visible_paths()))
def test_every_visible_command_path_exposes_help(command, capsys):
    rc = cli.main([*command, "--help"])
    assert rc == 0
    assert "usage:" in capsys.readouterr().out.lower()


def test_cli_reference_indexes_the_live_canonical_surface():
    text = (_REPO_ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")
    assert "# CLI Reference" in text
    for path in _visible_paths():
        nodes = COMMAND_TREE.nodes
        for segment in path:
            current = next(item for item in nodes if item.name == segment)
            nodes = current.children
        assert current.docs_anchor.startswith("docs/")


def test_cli_reference_routes_recipes_and_eval_by_workflow():
    landing = (_REPO_ROOT / "docs" / "CLI.md").read_text(encoding="utf-8")
    assert "| Catalog, artifacts, and recipes | `models` |" in landing
    assert "[Models & recipes: Recipes](cli/models.md#recipes)" in landing

    eval_reference = (_REPO_ROOT / "docs" / "cli" / "eval.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "## Choose the workflow",
        "## Target an endpoint",
        "## Preflight",
        "## Reasoning controls and budgets",
        "## Benchmark",
        "## Benchmark evidence",
        "## Usage, bootstrap, and calibration",
        "## External benchmarks",
        "## Migration",
    ):
        assert heading in eval_reference
    for command in (
        "eval preflight",
        "eval benchmark capacity",
        "eval benchmark quality",
        "eval benchmark evidence list",
        "eval benchmark evidence show",
        "eval benchmark evidence compare",
        "eval usage",
        "eval bootstrap",
        "eval calibrate",
    ):
        assert command in eval_reference

    external_reference = (_REPO_ROOT / "docs" / "EXTERNAL-BENCHMARKS.md").read_text(
        encoding="utf-8"
    )
    for action in (
        "init",
        "sources",
        "fetch",
        "import",
        "list",
        "report",
        "export",
        "compare",
    ):
        assert f"eval benchmark external {action}" in external_reference
    for action in ("add", "list", "render"):
        assert f"eval benchmark external notebook {action}" in external_reference


def test_active_cli_docs_do_not_advertise_tombstoned_mcp_forms():
    bare_mcp = re.compile(r"\banvil-serving mcp\b(?!\s+(?:serve|tools)\b)")
    legacy_tools = re.compile(r"\bmcp\s+(?:--list-tools|list-tools)\b")

    for path in _active_cli_document_paths():
        text = path.read_text(encoding="utf-8")
        if path == _REPO_ROOT / "docs" / "CLI.md":
            text, separator, _ = text.partition("## Migration from legacy commands")
            assert separator
        relative = path.relative_to(_REPO_ROOT)
        assert bare_mcp.search(text) is None, relative
        assert legacy_tools.search(text) is None, relative


def test_cli_consolidation_inventory_records_production_polish_audit():
    text = (Path(__file__).parents[1] / "docs" / "CLI-CONSOLIDATION-INVENTORY.md").read_text(
        encoding="utf-8"
    )
    assert "49 zero-context diff hunks" in text
    for path in (
        "CHANGELOG.md",
        "README.md",
        "anvil_serving/cli.py",
        "anvil_serving/serves.py",
        "anvil_serving/voice/cli.py",
        "docs/CLI-CONSOLIDATION-INVENTORY.md",
        "docs/CLI.md",
        "docs/VOICE.md",
        "tests/test_cli.py",
        "tests/voice/test_voice_cli.py",
    ):
        assert "`%s`" % path in text
    assert "convert these to tombstone tests" in text


# --------------------------------------------------------------------------- #
# `anvil-serving calibrate` — operator entry to the guarded write-back batch
# (flexibility:T006). Every test here is HERMETIC: the guard refuses BEFORE any
# network, or run_live is injected as a fake — CI makes ZERO tier/`claude` calls.
# --------------------------------------------------------------------------- #

# Minimal valid router config with one LOCAL tier (model set -> no 404 warning).
_LOCAL_TIER_CONFIG = """\
[router]
mapping_version = "test.calibrate.0"

[[router.tiers]]
id            = "fast-local"
base_url      = "http://127.0.0.1:30001/v1"
model         = "test-model"
dialect       = "openai"
context_limit = 32768
privacy       = "local"
tool_support  = true
auth_env      = "ANVIL_FAST_LOCAL_KEY"

[router.presets]
planning = ["fast-local"]
"""

# A LOCAL + CLOUD topology: the verb must pass BOTH to run_live (cloud filtering
# is run_live's job, not the verb's).
_LOCAL_AND_CLOUD_CONFIG = (
    _LOCAL_TIER_CONFIG
    + """
[[router.tiers]]
id            = "cloud"
base_url      = "https://api.anthropic.com"
model         = "claude-opus-4-20250514"
dialect       = "anthropic"
context_limit = 200000
privacy       = "cloud"
tool_support  = true
auth_env      = "ANTHROPIC_API_KEY"
"""
)


def _write_config(tmp_path, body=_LOCAL_TIER_CONFIG):
    cfg = tmp_path / "router.toml"
    cfg.write_text(body, encoding="utf-8")
    return str(cfg)


def _block_network(monkeypatch):
    """Fail hard if any socket is opened — proves the guard refuses before dialing."""

    def boom(*a, **k):  # pragma: no cover - must never fire
        raise AssertionError("calibrate attempted a network connection")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def _clear_mode_env(monkeypatch):
    for var in (
        "ANVIL_MODE",
        "ANVIL_MODES_CONFIG",
        "ANVIL_CONFIG_AGENTIC",
        "ANVIL_CONFIG_FLEXIBILITY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_calibrate_help_documents_verb_and_flags(capsys):
    """AC1: `calibrate --help` documents the config source, --out, the guard, prompts."""
    with pytest.raises(SystemExit) as exc:
        calibrate_mod.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in (
        "--config",
        "--mode",
        "--out",
        "--endpoint",
        "--dry-run",
        "--eval-data",
    ):
        assert token in out, token
    # The verb's purpose is described (guarded, reviewable candidate, no auto-promote).
    assert "candidate" in out.lower()


def test_calibrate_requires_a_config_selector(tmp_path, monkeypatch, capsys):
    """AC2: bare calibrate (no --config/--mode/env) is a usage error — never a
    silent default; run_live is never reached."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main([
        "--out", str(tmp_path / "c.json"),
        "--eval-data", calibrate_mod.DEFAULT_EVAL_DATA,
    ])
    assert rc == 2
    assert called == []
    assert "no config selected" in capsys.readouterr().err


def test_calibrate_refuses_without_confirmation(tmp_path, monkeypatch, capsys):
    """AC2/AC4: with a config + endpoint but NO confirmation, run_live's guard
    refuses cleanly (exit 2) before any tier/judge call — no network, no file."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    out = tmp_path / "candidate.json"
    rc = calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(out),
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            # deliberately NO --i-understand-this-calls-real-tiers
        ]
    )
    assert rc == 2
    assert not out.exists()  # nothing written, nothing measured
    assert "not configured to run" in capsys.readouterr().err


def test_calibrate_refuses_without_endpoints(tmp_path, monkeypatch, capsys):
    """AC2/AC4: confirmation alone (no --endpoint) still refuses — the endpoints
    that CONFIRM which tiers to dial are mandatory."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    out = tmp_path / "candidate.json"
    rc = calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(out),
            "--i-understand-this-calls-real-tiers",
            # deliberately NO --endpoint
        ]
    )
    assert rc == 2
    assert not out.exists()
    assert "--endpoint must cover every local tier: fast-local" in capsys.readouterr().err


def test_calibrate_malformed_endpoint_is_a_clean_error(tmp_path, monkeypatch, capsys):
    """A bad --endpoint spec is a clean exit 2, not a traceback."""
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    rc = calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(tmp_path / "c.json"),
            "--endpoint",
            "no-equals-sign",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert rc == 2
    assert "TIER=URL" in capsys.readouterr().err


def test_calibrate_rejects_invalid_endpoint_port(tmp_path, monkeypatch, capsys):
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--eval-data", calibrate_mod.DEFAULT_EVAL_DATA,
        "--out", str(tmp_path / "candidate.json"),
        "--endpoint", "fast-local=http://127.0.0.1:not-a-port/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 2
    assert "invalid port" in capsys.readouterr().err


def test_calibrate_rejects_endpoint_that_differs_from_config(
    tmp_path, monkeypatch, capsys
):
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **kwargs: called.append(kwargs))
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--eval-data", calibrate_mod.DEFAULT_EVAL_DATA,
        "--out", str(tmp_path / "candidate.json"),
        "--endpoint", "fast-local=http://127.0.0.1:39999/v1",
        "--i-understand-this-calls-real-tiers",
    ])
    assert rc == 2
    assert called == []
    assert "does not match configured backend" in capsys.readouterr().err


def test_calibrate_dry_run_never_calls_tiers_judge_or_writer(
    tmp_path, monkeypatch, capsys
):
    _clear_mode_env(monkeypatch)
    _block_network(monkeypatch)
    monkeypatch.setattr(
        calibrate_mod,
        "run_live",
        lambda **kwargs: pytest.fail("dry-run called run_live"),
    )
    out = tmp_path / "candidate.json"
    rc = calibrate_mod.main([
        "--config", _write_config(tmp_path),
        "--eval-data", calibrate_mod.DEFAULT_EVAL_DATA,
        "--out", str(out),
        "--endpoint", "fast-local=http://127.0.0.1:30001/v1",
        "--dry-run",
    ])
    assert rc == 0
    assert not out.exists()
    rendered = capsys.readouterr().out
    assert "calibration plan" in rendered
    assert "deferred: tier requests, independent judge calls, candidate write" in rendered


def test_calibrate_wires_run_live_and_prints_promote(tmp_path, monkeypatch, capsys):
    """AC2/AC4: with config + endpoint + confirmation, the verb loads the config's
    tiers, calls run_live with the guard args intact, and prints the review->promote
    instruction. run_live is a FAKE (no tier/judge call); nothing is auto-promoted."""
    _clear_mode_env(monkeypatch)
    from anvil_serving.router import config as rconfig

    cfg_path = _write_config(tmp_path, _LOCAL_AND_CLOUD_CONFIG)
    loaded = rconfig.load(cfg_path)
    out = tmp_path / "candidate.json"

    seen = {}

    def fake_run_live(**kwargs):
        seen.update(kwargs)
        # A real run_live writes the candidate; mimic that so the summary path runs.
        kwargs["out_path"].write_text(
            json.dumps(
                {
                    "schema": "x",
                    "mode": "live",
                    "entries": [{"tier_id": "fast-local", "work_class": "planning"}],
                }
            ),
            encoding="utf-8",
        )
        return None  # the verb ignores the return; it works off the written file

    monkeypatch.setattr(calibrate_mod, "run_live", fake_run_live)

    rc = calibrate_mod.main(
        [
            "--config",
            cfg_path,
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(out),
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert rc == 0

    # The guard args reached run_live intact, with the config's tiers (BOTH the
    # local and cloud tier — the verb does not pre-filter; that is run_live's job).
    assert seen["confirm_calls_real_tiers"] is True
    assert seen["endpoints"] == {"fast-local": "http://127.0.0.1:30001/v1"}
    assert seen["tiers"] == loaded.tiers
    assert {t.id for t in seen["tiers"]} == {"fast-local", "cloud"}
    assert seen["out_path"] == out
    # The resolved mode is forwarded to run_live (None for a --config boot) so the
    # candidate fingerprints match the live serve's mode (ADR-0011 / T013).
    assert seen["mode"] is None

    # The review -> promote instruction is printed and points [router].profile_path
    # at the candidate; nothing was auto-promoted.
    printed = capsys.readouterr().out
    assert "profile_path" in printed
    assert str(out) in printed
    assert "Nothing was promoted" in printed
    assert "1 measured row(s)" in printed


def test_calibrate_rejects_missing_out_dir_before_running_batch(tmp_path, monkeypatch, capsys):
    """A missing --out directory is rejected BEFORE the expensive live batch, not as
    a late write error after real tiers were dialed. run_live must never be called."""
    _clear_mode_env(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(tmp_path / "nope" / "candidate.json"),  # 'nope' dir does not exist
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert rc == 2
    assert called == []  # the batch never ran — no measurement work lost
    assert "output directory does not exist" in capsys.readouterr().err


def test_calibrate_rejects_missing_eval_data_dir(tmp_path, monkeypatch, capsys):
    """A missing --eval-data directory is rejected cleanly (exit 2), not surfaced as a
    late FileNotFoundError traceback after the batch starts. run_live never runs. (Copilot #119)"""
    _clear_mode_env(monkeypatch)
    called = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: called.append(k))
    rc = calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--out",
            str(tmp_path / "candidate.json"),
            "--eval-data",
            str(tmp_path / "no-such-fixtures"),  # missing dir
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert rc == 2
    assert called == []
    assert "eval-data directory does not exist" in capsys.readouterr().err


def test_calibrate_dispatches_through_cli(tmp_path, monkeypatch):
    """The verb is wired into the top-level CLI dispatch (`anvil-serving calibrate`)."""
    _clear_mode_env(monkeypatch)
    calls = []
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: calls.append(k) or None)
    rc = cli.main(
        [
            "eval",
            "calibrate",
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(tmp_path / "c.json"),
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--confirm",
        ]
    )
    assert rc == 0
    assert len(calls) == 1


def test_calibrate_forwards_max_tokens_when_set(tmp_path, monkeypatch):
    """--max-tokens overrides run_live's default budget; unset -> not forwarded."""
    _clear_mode_env(monkeypatch)
    seen = {}
    monkeypatch.setattr(calibrate_mod, "run_live", lambda **k: seen.update(k) or None)

    calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(tmp_path / "c.json"),
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--i-understand-this-calls-real-tiers",
            "--max-tokens",
            "8192",
        ]
    )
    assert seen["max_tokens"] == 8192

    seen.clear()
    calibrate_mod.main(
        [
            "--config",
            _write_config(tmp_path),
            "--eval-data",
            calibrate_mod.DEFAULT_EVAL_DATA,
            "--out",
            str(tmp_path / "c.json"),
            "--endpoint",
            "fast-local=http://127.0.0.1:30001/v1",
            "--i-understand-this-calls-real-tiers",
        ]
    )
    assert "max_tokens" not in seen  # unset -> run_live's own default applies
