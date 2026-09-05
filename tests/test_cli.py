"""Tests for the anvil-serving CLI dispatch — in particular the early
Python-version guard (`anvil_serving.cli._check_python_version`).
"""

import json
import re
import shlex
from pathlib import Path

import pytest

from anvil_serving import cli
from anvil_serving import controller_diagnostics
from anvil_serving import harness
from anvil_serving import host
from anvil_serving import benchmark, multiplexer, preflight
from anvil_serving import router_manage
from anvil_serving import serves
from anvil_serving.commands import COMMAND_TREE, CommandNode, CommandOption, HandlerRef


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _public_inspect_result() -> dict[str, object]:
    container_id = "a" * 64
    return {
        **controller_diagnostics.safe_result(
            "inspect", "ok", container_id=container_id
        ),
        "running": True,
        "exit_code": 0,
        "health": "healthy",
        "configured_bindings": [
            {"container_port": 8080, "host_port": 8080, "bind_class": "loopback"}
        ],
        "observed_bindings": [],
    }


def _public_logs_result(
    state: str = "ok", *, container_id: str | None = None
) -> dict[str, object]:
    if state == "ok" and container_id is None:
        container_id = "b" * 64
    return {
        **controller_diagnostics.safe_result(
            "logs", state, container_id=container_id
        ),
        "events": [],
        "line_count": 0,
        "returned_events": 0,
        "rejected_lines": 0,
        "unknown_fields": 0,
        "unknown_codes": 0,
        "counters_saturated": False,
    }


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


def test_cli_local_diagnostic_json_uses_only_validated_canonical_result(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: None)
    monkeypatch.setattr(
        controller_diagnostics,
        "inspect_controller",
        lambda _container: _public_inspect_result(),
    )

    assert cli.main([
        "--json", "controller", "inspect", "--container", "private-container-marker"
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "controller inspect"
    assert payload["context"] is None
    assert payload["warnings"] == []
    assert payload["data"] == _public_inspect_result()
    assert "private-container-marker" not in json.dumps(payload)


def test_cli_local_diagnostic_parser_refusal_does_not_invoke_or_echo_operands(
    monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: None)
    monkeypatch.setattr(
        controller_diagnostics,
        "controller_logs",
        lambda *_args: pytest.fail("diagnostic invoked after invalid tail"),
    )

    assert cli.main([
        "--json",
        "controller",
        "logs",
        "--container",
        "private-container-marker",
        "--tail",
        "1_0-private-tail-marker",
    ]) == 2
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert rendered.err == ""
    assert payload["command"] == "controller logs"
    assert payload["context"] is None and payload["warnings"] == []
    assert payload["data"] is None
    assert payload["error"]["code"] == "invalid_diagnostic_arguments"
    assert "private-container-marker" not in rendered.out + rendered.err
    assert "private-tail-marker" not in rendered.out + rendered.err


@pytest.mark.parametrize("json_mode", (False, True))
def test_cli_local_diagnostic_literal_separator_help_is_captured_and_operand_free(
    monkeypatch, capsys, json_mode
):
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: None)
    monkeypatch.setattr(
        controller_diagnostics,
        "controller_logs",
        lambda *_args: pytest.fail("diagnostic invoked after literal separator"),
    )
    argv = [
        "controller",
        "logs",
        "--container",
        "safe",
        "--",
        "--help",
        "private-after-separator-marker",
    ]
    if json_mode:
        argv.insert(0, "--json")

    assert cli.main(argv) == 2
    rendered = capsys.readouterr()
    assert "private-after-separator-marker" not in rendered.out + rendered.err
    if json_mode:
        payload = json.loads(rendered.out)
        assert payload["data"] is None
        assert payload["error"]["code"] == "invalid_diagnostic_arguments"
        assert payload["context"] is None and payload["warnings"] == []
    else:
        assert rendered.out == ""
        assert rendered.err == "invalid diagnostic arguments\n"


def test_cli_remote_diagnostic_unwraps_only_exact_transport_core(
    tmp_path, monkeypatch, capsys
):
    topology = _write_remote_controller_topology(tmp_path, "controller-inspect")
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda plan, operation, **_kwargs: cli.TransportResult(
            operation.name,
            "controller",
            {"ok": True, "data": _public_inspect_result()},
        ),
    )

    assert cli.main([
        "--json",
        "controller",
        "inspect",
        "--topology",
        str(topology),
        "--container",
        "private-container-marker",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "controller inspect"
    assert payload["context"] is None and payload["warnings"] == []
    assert payload["data"] == _public_inspect_result()
    assert "private-container-marker" not in json.dumps(payload)
    assert str(topology) not in json.dumps(payload)


@pytest.mark.parametrize("json_mode", (False, True))
def test_cli_remote_diagnostic_rejects_unvalidated_nested_data_without_echo(
    tmp_path, monkeypatch, capsys, json_mode
):
    topology = _write_remote_controller_topology(tmp_path, "controller-inspect")
    hostile = _public_inspect_result()
    hostile["configured_bindings"] = [
        {
            "container_port": 8080,
            "host_port": 8080,
            "bind_class": "loopback",
            "private_nested_marker": "private-nested-marker",
        }
    ]
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda plan, operation, **_kwargs: cli.TransportResult(
            operation.name, "controller", {"ok": True, "data": hostile}
        ),
    )

    argv = [
        "controller", "inspect", "--topology", str(topology), "--container", "safe"
    ]
    if json_mode:
        argv.insert(0, "--json")
    assert cli.main(argv) == 4
    rendered = capsys.readouterr()
    assert "private-nested-marker" not in rendered.out + rendered.err
    if json_mode:
        payload = json.loads(rendered.out)
        assert payload["data"] is None
        assert payload["error"]["code"] == "controller_diagnostic_response_invalid"
    else:
        assert rendered.out == ""
        assert rendered.err == "controller diagnostic response invalid\n"


def test_cli_remote_diagnostic_rejects_hostile_outer_key_before_comparison(
    tmp_path, monkeypatch, capsys
):
    class HostileString(str):
        __hash__ = str.__hash__

        def __eq__(self, _other):
            raise RuntimeError("private-outer-comparison-marker")

    topology = _write_remote_controller_topology(tmp_path, "controller-inspect")
    hostile = {HostileString("ok"): True, "data": _public_inspect_result()}
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda plan, operation, **_kwargs: cli.TransportResult(
            operation.name, "controller", hostile
        ),
    )

    assert cli.main(
        [
            "--json",
            "controller",
            "inspect",
            "--topology",
            str(topology),
            "--container",
            "safe",
        ]
    ) == 4
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert payload["error"]["code"] == "controller_diagnostic_response_invalid"
    assert "private-outer-comparison-marker" not in rendered.out + rendered.err


def test_cli_diagnostic_resolution_failure_is_fixed_and_operand_free(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_resolve_dispatch_plan",
        lambda *_args: (_ for _ in ()).throw(
            cli.TargetResolutionError("private-resolution-marker")
        ),
    )

    assert cli.main([
        "--json", "controller", "inspect", "--container", "private-container-marker"
    ]) == 2
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert payload["command"] == "controller inspect"
    assert payload["context"] is None and payload["warnings"] == []
    assert payload["data"] is None
    assert payload["error"]["code"] == "invalid_diagnostic_arguments"
    assert "private-resolution-marker" not in rendered.out + rendered.err
    assert "private-container-marker" not in rendered.out + rendered.err


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.__setitem__("unexpected", "private-extra-marker"),
        lambda value: value["configured_bindings"][0].__setitem__("host_port", 0),
        lambda value: value.__setitem__("running", 1),
        lambda value: value.__setitem__("container_id", "not-a-container-id"),
    ),
)
def test_diagnostic_public_validator_rejects_load_bearing_malformed_inspection(mutate):
    value = _public_inspect_result()
    mutate(value)
    with pytest.raises(ValueError, match="invalid diagnostic public result"):
        controller_diagnostics.validate_public_result(value, expected_kind="inspect")


@pytest.mark.parametrize(
    ("kind", "value"),
    (
        ("inspect", controller_diagnostics.safe_result("inspect", "unavailable")),
        ("logs", controller_diagnostics.safe_result("logs", "ok", container_id="c" * 64)),
    ),
)
def test_diagnostic_public_validator_requires_complete_kind_schema(kind, value):
    with pytest.raises(ValueError, match="invalid diagnostic public result"):
        controller_diagnostics.validate_public_result(value, expected_kind=kind)


def test_diagnostic_public_validator_rejects_hostile_scalar_without_comparison(
    monkeypatch, capsys
):
    class HostileString(str):
        def __ne__(self, _other):
            raise RuntimeError("private-comparison-marker")

    value = _public_inspect_result()
    value["schema_version"] = HostileString(value["schema_version"])
    with pytest.raises(ValueError, match="invalid diagnostic public result") as exc:
        controller_diagnostics.validate_public_result(value, expected_kind="inspect")
    assert "private-comparison-marker" not in str(exc.value)

    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda *_args: None)
    monkeypatch.setattr(
        controller_diagnostics, "inspect_controller", lambda _container: value
    )
    assert cli.main(
        ["--json", "controller", "inspect", "--container", "safe"]
    ) == 4
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert payload["error"]["code"] == "controller_diagnostic_response_invalid"
    assert "private-comparison-marker" not in rendered.out + rendered.err


@pytest.mark.parametrize("counter", (False, 0.0))
def test_diagnostic_public_validator_rejects_coerced_failure_counters(counter):
    value = _public_logs_result("unavailable")
    value["line_count"] = counter
    with pytest.raises(ValueError, match="invalid diagnostic public result"):
        controller_diagnostics.validate_public_result(value, expected_kind="logs")


def test_diagnostic_public_validator_enforces_success_identity_and_saturation():
    missing_identity = _public_logs_result()
    missing_identity["container_id"] = None
    with pytest.raises(ValueError, match="invalid diagnostic public result"):
        controller_diagnostics.validate_public_result(
            missing_identity, expected_kind="logs"
        )

    impossible_saturation = _public_logs_result()
    impossible_saturation["counters_saturated"] = True
    with pytest.raises(ValueError, match="invalid diagnostic public result"):
        controller_diagnostics.validate_public_result(
            impossible_saturation, expected_kind="logs"
        )

    exact_cap = _public_logs_result()
    exact_cap["unknown_fields"] = controller_diagnostics._MAX_COUNTER
    assert controller_diagnostics.validate_public_result(
        exact_cap, expected_kind="logs"
    )["counters_saturated"] is False
    exact_cap["counters_saturated"] = True
    assert controller_diagnostics.validate_public_result(
        exact_cap, expected_kind="logs"
    )["counters_saturated"] is True


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
        "Start here:",
        "Model Serving:",
        "Capability Gateway:",
        "Evaluation & Evidence:",
        "Anvil Voice:",
        "Anvil Media:",
        "Control Plane & Fleet:",
        "Global options:",
        "--command-manifest",
        "anvil-serving --version",
        "router run",
        "eval preflight",
        "anvil-serving serves status",
        "--tier primary --dry-run",
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


@pytest.mark.parametrize(
    "path",
    (
        (),
        ("eval",),
        ("eval", "benchmark"),
        ("eval", "benchmark", "evidence"),
        ("eval", "benchmark", "external"),
        ("eval", "benchmark", "external", "notebook"),
    ),
)
def test_root_and_eval_parent_help_respect_narrow_windows_console(path, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "60")

    assert cli.main([*path, "--help"]) == 0
    text = capsys.readouterr().out

    text.encode("cp1252")
    assert all(len(line) <= 60 for line in text.splitlines())


@pytest.mark.parametrize("flag", ["-V", "--version"])
def test_top_level_version_reports_installed_version(flag, capsys):
    rc = cli.main([flag])
    assert rc == 0
    assert capsys.readouterr().out == "anvil-serving %s\n" % cli.__version__


def test_package_version_matches_pyproject():
    import tomllib

    with open(_REPO_ROOT / "pyproject.toml", "rb") as fh:
        pyproject = tomllib.load(fh)
    assert cli.__version__ == pyproject["project"]["version"], (
        "anvil_serving/__init__.py __version__ and pyproject.toml version must be bumped together"
    )


def test_command_manifest_is_terminal_and_machine_readable(capsys):
    assert cli.main(["--command-manifest"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 6
    assert len(payload["product_families"]) == 6
    assert payload["umbrella"]["name"] == "Anvil Serving"
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


def test_mcp_canonical_paths_dispatch(monkeypatch):
    from anvil_serving import mcp

    calls = []
    monkeypatch.setattr(mcp, "main", lambda argv: calls.append(argv) or 0)

    assert cli.main(["mcp", "tools"]) == 0
    assert cli.main(["mcp", "serve"]) == 0
    assert calls == [["list-tools"], []]


def test_global_json_wraps_root_and_nested_dispatch(capsys):
    assert cli.main(["--json", "--help"]) == 0
    root = json.loads(capsys.readouterr().out)
    assert root["ok"] is True
    assert "anvil-serving - operate, qualify" in root["data"]

    assert cli.main(["mcp", "tools", "--json"]) == 0
    nested = json.loads(capsys.readouterr().out)
    assert nested["ok"] is True
    assert "router_status" in nested["data"]


def test_product_discovery_is_human_and_typed_json(capsys):
    assert cli.main(["product", "families"]) == 0
    human = capsys.readouterr().out
    assert "Anvil Serving" in human
    assert "Anvil Media (anvil-media)" in human
    assert "Control Plane & Fleet" in human

    assert cli.main(["product", "journey", "media", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["family"]["id"] == "anvil-media"
    assert payload["data"]["family"]["journey"][0]["cli"] == ("anvil-serving media capabilities")


def test_unknown_product_family_is_a_typed_usage_error(capsys):
    assert cli.main(["product", "journey", "not-a-family", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "unknown_product_family"


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


def test_json_error_preserves_bounded_legacy_stdout(monkeypatch, capsys):
    def failing_handler(argv):
        assert argv == ["lint"]
        print("ERROR missing-registry candidate-a")
        return 1

    monkeypatch.setattr(HandlerRef, "resolve", lambda self: failing_handler)

    assert cli.main(["serves", "lint", "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["data"] == "ERROR missing-registry candidate-a\n"


def test_json_mutation_never_prompts_and_requires_confirmation(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("prompted in JSON mode"))
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: pytest.fail("handler resolved"))
    assert cli.main(["router", "up", "--json"]) == 3
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert payload["error"]["class"] == "safety"
    assert "--confirm" in payload["error"]["message"]


def test_serves_mode_confirm_is_forwarded_to_the_local_legacy_handler(monkeypatch):
    # `serves mode enter|leave` declare forward_confirm_flag: the legacy leaf
    # parser gates on its own --confirm option, so the dispatcher restores the
    # consumed token in argv. Preview/status never receive one.
    calls = []
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: lambda argv: calls.append(argv) or 0,
    )

    for action in ("enter", "leave"):
        assert (
            cli.main(
                [
                    "serves",
                    "mode",
                    action,
                    "tp2",
                    "--restore-group",
                    "split-stack",
                    "--confirm",
                ]
            )
            == 0
        )
    assert (
        cli.main(
            [
                "serves",
                "mode",
                "preview",
                "tp2",
                "--restore-group",
                "split-stack",
            ]
        )
        == 0
    )
    assert cli.main(["serves", "mode", "status"]) == 0

    enter, leave, preview, status = calls
    assert enter[:3] == ["mode", "enter", "tp2"]
    assert enter.count("--confirm") == 1
    assert leave[:3] == ["mode", "leave", "tp2"]
    assert leave.count("--confirm") == 1
    assert "--confirm" not in preview
    assert "--confirm" not in status


def test_serves_mode_enter_json_never_prompts_and_requires_confirmation(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt: pytest.fail("prompted in JSON mode"))
    monkeypatch.setattr(HandlerRef, "resolve", lambda self: pytest.fail("handler resolved"))
    assert (
        cli.main(
            [
                "serves",
                "mode",
                "enter",
                "tp2",
                "--restore-group",
                "split-stack",
                "--json",
            ]
        )
        == 3
    )
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
address = "100.64.0.10"
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
endpoint = "http://100.64.0.10:8765"
auth_env = "ANVIL_CONTROLLER_TOKEN"
allowed_operations = ["{operation}"]
""",
        encoding="utf-8",
    )
    return topology


def _write_remote_controller_topology(tmp_path: Path, operation: str) -> Path:
    topology = _write_remote_router_topology(tmp_path, operation)
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["controller"]')
    text = text.replace('role = "router"', 'role = "controller"')
    topology.write_text(text, encoding="utf-8")
    return topology


def test_remote_integer_scalar_requires_ascii_grammar_and_preserves_other_types():
    integer_schema = {"type": "integer", "minimum": -10, "maximum": 10}
    assert cli._remote_scalar("--value", "0001", integer_schema) == 1
    assert cli._remote_scalar("--value", "-07", integer_schema) == -7
    assert cli._remote_scalar("--name", " 1", {"type": "string"}) == " 1"
    assert cli._remote_scalar("--ratio", "+1", {"type": "number"}) == 1.0
    for raw in ("+1", "1_0", " 1", "\u0661"):
        with pytest.raises(cli.UsageError, match="requires a numeric value"):
            cli._remote_scalar("--value", raw, integer_schema)


@pytest.mark.parametrize(
    ("tail", "expected_arguments"),
    [
        (None, {"container": "controller_1"}),
        ("1", {"container": "controller_1", "tail": 1}),
        ("200", {"container": "controller_1", "tail": 200}),
    ],
)
def test_cli_remote_controller_logs_dispatches_only_valid_tails(
    tmp_path, monkeypatch, tail, expected_arguments
):
    topology = _write_remote_controller_topology(tmp_path, "controller-logs")
    seen = {}

    def fake_execute(plan, operation, **_kwargs):
        seen["plan"] = plan
        seen["operation"] = operation
        return cli.TransportResult(
            operation.name,
            "controller",
            {"ok": True, "data": _public_logs_result()},
        )

    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote diagnostic dispatch imported the local handler"),
    )
    argv = [
        "controller",
        "logs",
        "--topology",
        str(topology),
        "--container",
        "controller_1",
    ]
    if tail is not None:
        argv.extend(("--tail", tail))

    assert cli.main(argv) == 0
    assert seen["plan"].transport == "controller"
    assert seen["operation"].name == "controller-logs"
    assert seen["operation"].tool_name == "controller_logs"
    assert dict(seen["operation"].arguments) == expected_arguments


@pytest.mark.parametrize("raw_tail", ("+1", "1_0", " 1", "\u0661", "0", "201"))
def test_cli_remote_controller_logs_rejects_invalid_tails_before_transport(
    tmp_path, monkeypatch, capsys, raw_tail
):
    topology = _write_remote_controller_topology(tmp_path, "controller-logs")
    monkeypatch.setattr(
        cli,
        "execute_plan",
        lambda *_args, **_kwargs: pytest.fail("invalid tail reached controller transport"),
    )
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("invalid tail imported the local diagnostic handler"),
    )

    assert (
        cli.main(
            [
                "controller",
                "logs",
                "--topology",
                str(topology),
                "--container",
                "controller_1",
                "--tail",
                raw_tail,
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert error
    assert raw_tail not in error


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
        "http://100.64.0.10:8765",
        {
            "auth_env": "ANVIL_CONTROLLER_TOKEN",
            "allowed_operations": ("router-restart",),
            "timeout_seconds": 60.0,
            "expected_node": None,
        },
    )
    assert seen["execute_kwargs"]["idempotency_key"].startswith("cli-")
    assert "transport=controller" in capsys.readouterr().out


def test_cli_remote_config_export_maps_path_and_allows_bounded_large_response(
    tmp_path, monkeypatch, capsys
):
    topology = _write_remote_router_topology(tmp_path, "host-config-export")
    text = topology.read_text(encoding="utf-8")
    text = text.replace('roles = ["router"]', 'roles = ["host"]')
    text = text.replace('role = "router"', 'role = "host"')
    topology.write_text(text, encoding="utf-8")
    seen = {}

    class FakeController:
        def __init__(self, endpoint, **kwargs):
            seen["controller"] = (endpoint, kwargs)

    def fake_execute(plan, operation, **kwargs):
        seen["operation"] = operation
        return cli.TransportResult(operation.name, "controller", {"ok": True})

    monkeypatch.setattr(cli, "ControllerTransport", FakeController)
    monkeypatch.setattr(cli, "execute_plan", fake_execute)
    monkeypatch.setattr(
        HandlerRef,
        "resolve",
        lambda self: pytest.fail("remote config export imported the local handler"),
    )

    assert (
        cli.main(
            [
                "host",
                "config",
                "export",
                "--topology",
                str(topology),
                "--path",
                "serve-recipes.toml",
                "--path",
                "host.toml",
            ]
        )
        == 0
    )

    assert seen["operation"].tool_name == "operator_config_export"
    assert dict(seen["operation"].arguments) == {"paths": ["serve-recipes.toml", "host.toml"]}
    assert seen["controller"] == (
        "http://100.64.0.10:8765",
        {
            "auth_env": "ANVIL_CONTROLLER_TOKEN",
            "allowed_operations": ("host-config-export",),
            "timeout_seconds": 60.0,
            "expected_node": None,
            "max_response_bytes": 1024 * 1024,
        },
    )
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
    assert (
        cli._remote_transport_timeout(
            {"timeout_seconds": 30, "checks": "smoke,json"},
            tool_name="preflight_probe",
        )
        == 65.0
    )
    assert (
        cli._remote_transport_timeout({"timeout_seconds": 30}, tool_name="preflight_probe") == 125.0
    )
    assert (
        cli._remote_transport_timeout(
            {"timeout_seconds": 3600, "checks": "smoke", "dry_run": True},
            tool_name="preflight_probe",
        )
        == 60.0
    )
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
            ["harness", "sync", "clients"],
            "harness-sync-clients",
            "client_catalog_sync",
            {
                "base_url": "https://router.example.ts.net/v1",
                "confirm": True,
                "dry_run": False,
                "hermes_profiles": "all",
                "restart_openclaw_on_change": True,
                "restart_hermes_on_change": True,
            },
        ),
        (
            ["harness", "sync", "hermes-media"],
            "harness-sync-hermes-media",
            "hermes_media_sync",
            {
                "confirm": True,
                "dry_run": False,
                "hermes_profiles": "default,anvil-primary",
                "restart_hermes_on_change": True,
            },
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
        "client_catalog_sync": [
            "--confirm",
            "--base-url",
            "https://router.example.ts.net/v1",
            "--hermes-profiles",
            "all",
            "--restart-openclaw-on-change",
            "--restart-hermes-on-change",
        ],
        "hermes_media_sync": [
            "--confirm",
            "--hermes-profiles",
            "default,anvil-primary",
            "--restart-hermes-on-change",
        ],
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
endpoint = "ssh://operator@100.64.0.10:22"
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
            self.host = "100.64.0.10"
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
    assert "controller=http://100.64.0.10:8765" in output


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


def test_voice_benchmark_is_endpoint_client_not_proxy_owner(monkeypatch, capsys):
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
        == 2
    )
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["error"]["code"] == "usage_error"
    assert "does not support target-resolution options" in rejected["error"]["message"]

    assert cli.main(["voice", "benchmark", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context"]["resource"] is None
    assert payload["context"]["execution_host"] is None
    assert payload["context"]["resource_endpoint"] is None


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


def test_focused_action_help_for_operational_verbs(capsys):
    assert router_manage.main(["logs", "--help"]) == 2
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
    assert "--gateway-host" in out

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
        "Stop and remove manifest serve containers",
        "explicit confirmation",
        "externally-started serves",
        "streaming docker logs",
        "Render tuned compose",
    ):
        assert token in out


def test_focused_action_help_includes_action_specific_flags(capsys):
    with pytest.raises(SystemExit) as exc:
        harness.main(["sync", "openclaw", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for token in (
        "--config",
        "--out",
        "--base-url",
        "--api-key-env",
        "--overwrite",
        "--voice",
        "--voice-realtime-url",
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
    assert "| Model Serving | `init`, `models`, `serves` |" in landing
    assert "[Models & recipes: Recipes](cli/models.md#recipes)" in landing

    eval_reference = (_REPO_ROOT / "docs" / "cli" / "eval.md").read_text(encoding="utf-8")
    for command in (
        "eval preflight",
        "eval benchmark capacity",
        "eval benchmark quality",
        "eval benchmark external",
        "eval usage",
    ):
        assert command in eval_reference
    for removed in ("eval bootstrap", "eval calibrate", "eval planning"):
        assert removed not in eval_reference

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


def test_active_cli_docs_use_canonical_mcp_forms():
    bare_mcp = re.compile(r"\banvil-serving mcp\b(?!\s+(?:serve|tools)\b)")
    legacy_tools = re.compile(r"\bmcp\s+(?:--list-tools|list-tools)\b")

    for path in _active_cli_document_paths():
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(_REPO_ROOT)
        assert bare_mcp.search(text) is None, relative
        assert legacy_tools.search(text) is None, relative
