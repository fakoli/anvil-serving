"""Tests for the anvil-serving CLI dispatch — in particular the early
Python-version guard (`anvil_serving.cli._check_python_version`).
"""

import json
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
from anvil_serving.commands import COMMAND_TREE, HandlerRef


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


def test_workload_cli_global_option_failure_is_operand_free(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_workload_now", lambda: pytest.fail("clock read after invalid global options")
    )
    assert cli.main([
        "--json",
        "--quiet",
        "--verbose",
        "fleet",
        "workloads",
        "--controller-url",
        "http://100.64.0.10:9000/private-url-marker",
        "--auth-env",
        "PRIVATE_MARKER_ENV",
        "--expected-node",
        "private-node-marker",
    ]) == 2
    rendered = capsys.readouterr()
    payload = json.loads(rendered.out)
    assert rendered.err == ""
    assert payload["command"] == "fleet workloads"
    assert payload["context"] is None and payload["warnings"] == []
    assert payload["data"] is None
    assert payload["error"]["code"] == "invalid_workload_arguments"
    assert "private" not in rendered.out.lower()


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
    topology = Path(__file__).parent.parent / "examples" / "primary-node" / "operator-topology.toml"
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
                "host:companion-node",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["context"]["execution_host"] == "companion-node"
    assert payload["context"]["resource"] == "mini-host"
    assert payload["data"]["resolved_command"] == "host-status"
    assert not isinstance(payload["data"], str)


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


def _visible_paths(nodes=COMMAND_TREE.nodes, prefix=()):
    for node in nodes:
        path = prefix + (node.name,)
        if node.visible:
            yield path
        yield from _visible_paths(node.children, path)
