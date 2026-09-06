"""Hermetic bounded-child coverage; no Docker daemon is invoked."""

import os
import sys
import threading
import io
import json

import pytest

from anvil_serving import cli, controller_diagnostics as diagnostics, mcp
from anvil_serving.commands.control_plane import commands as control_plane_commands
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import operations


def _child(source):
    return (sys.executable, "-c", source)


def test_validation_and_safe_common_result_are_closed():
    assert diagnostics.validate_container_name("controller_1") == "controller_1"
    assert diagnostics.validate_log_tail() == 100
    for value in ("-x", "a/b", "space name", "x\n", "x" * 129, 1):
        with pytest.raises(ValueError):
            diagnostics.validate_container_name(value)
    for value in (0, 201, True, "10"):
        with pytest.raises(ValueError):
            diagnostics.validate_log_tail(value)
    assert diagnostics.safe_result("logs", "timeout", truncated=True) == {
        "schema_version": "controller-diagnostics/v1",
        "kind": "logs",
        "state": "timeout",
        "error_code": "diagnostic_timeout",
        "container_id": None,
        "truncated": True,
    }
    assert diagnostics.safe_result("logs", "timeout")["truncated"] is True
    assert diagnostics.safe_result("logs", "output-limit")["truncated"] is True
    assert diagnostics.safe_result("logs", "ok", truncated=True)["truncated"] is True


def test_local_prefix_and_environment_are_pinned_and_scrub_docker_overrides():
    assert diagnostics.local_docker_prefix(platform="win32")[2] == "npipe:////./pipe/docker_engine"
    assert diagnostics.local_docker_prefix(platform="linux")[2] == "unix:///var/run/docker.sock"
    for platform in ("darwin", "freebsd", "posix", "nt"):
        with pytest.raises(ValueError):
            diagnostics.local_docker_prefix(platform=platform)
    seen = {}

    def factory(argv, **kwargs):
        seen.update(kwargs)
        return __import__("subprocess").Popen(_child("pass"), **kwargs)

    capture = diagnostics._capture_fixed_child(
        ("fixed",),
        merged=False,
        process_factory=factory,
        environment={"DOCKER_HOST": "remote", "docker_tls": "hostile", "PATH": os.environ["PATH"]},
    )
    assert capture.state == "ok"
    assert "DOCKER_HOST" not in seen["env"]
    assert "docker_tls" not in seen["env"]


def test_separate_flood_enforces_shared_ceiling_and_merged_keeps_order(monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_CAPTURE_BYTES", 1024)
    flood = (
        "import sys,threading;gate=threading.Barrier(3);"
        "a=threading.Thread(target=lambda:(gate.wait(),sys.stdout.write('x'*800),sys.stdout.flush()));"
        "b=threading.Thread(target=lambda:(gate.wait(),sys.stderr.write('y'*800),sys.stderr.flush()));"
        "a.start();b.start();gate.wait();a.join();b.join()"
    )
    capture = diagnostics._capture_fixed_child(_child(flood), merged=False)
    assert capture.state == "output-limit"
    assert len(capture.stdout) + len(capture.stderr) <= 1024
    giant = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('z'*2048)"), merged=False
    )
    assert giant.state == "output-limit" and len(giant.stdout) <= 1024
    merged = diagnostics._capture_fixed_child(
        _child(
            "import sys;sys.stdout.write('one\\n');sys.stdout.flush();sys.stderr.write('two\\n');sys.stderr.flush()"
        ),
        merged=True,
    )
    assert merged.state == "ok"
    assert merged.stdout.replace(b"\r\n", b"\n") == b"one\ntwo\n" and merged.stderr == b""


def test_missing_executable_timeout_and_nonzero_are_fixed_states(monkeypatch):
    assert (
        diagnostics._capture_fixed_child(("missing-no-such-child",), merged=False).state
        == "unavailable"
    )
    timeout = diagnostics._capture_fixed_child(
        _child("import time;time.sleep(1)"), merged=False,
        monotonic=iter((0.0, 11.0)).__next__, retain_stdout_on_error=True,
    )
    assert timeout.state == "timeout" and timeout.truncated is True and timeout.stdout == timeout.stderr == b""
    assert (
        diagnostics._capture_fixed_child(_child("import sys;sys.exit(3)"), merged=False).state
        == "unavailable"
    )
    retained = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('bounded-row\\n');sys.stderr.write('private') ;sys.exit(3)"),
        merged=False, retain_stdout_on_error=True,
    )
    assert retained.state == "unavailable" and retained.stdout.replace(b"\r\n", b"\n") == b"bounded-row\n"
    assert retained.stderr == b""


def test_exact_default_capture_ceiling_and_one_byte_overflow():
    exact = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('x'*262144)"), merged=False
    )
    assert exact.state == "ok" and len(exact.stdout) == diagnostics.MAX_CAPTURE_BYTES
    over = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('x'*262145)"), merged=False,
        retain_stdout_on_error=True,
    )
    assert over.state == "output-limit" and over.stdout == over.stderr == b""


class _BlockingPipe:
    def __init__(self):
        self.closed = threading.Event()

    def read(self, _size):
        self.closed.wait(1)
        return b""

    def close(self):
        self.closed.set()


class _FakeProcess:
    def __init__(self, *, exited=False, terminate_raises=False, kill_raises=False):
        self.stdout = _BlockingPipe()
        self.stderr = _BlockingPipe()
        self.exit_code = 0 if exited else None
        self.terminate_raises = terminate_raises
        self.kill_raises = kill_raises
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated += 1
        if self.terminate_raises:
            raise OSError("synthetic")
        self.exit_code = -15

    def kill(self):
        self.killed += 1
        if self.kill_raises:
            raise OSError("synthetic")
        self.exit_code = -9

    def wait(self, timeout):
        if self.exit_code is None:
            raise TimeoutError
        return self.exit_code


def test_early_parent_exit_waits_for_reader_eof_and_never_returns_ok():
    process = _FakeProcess(exited=True)
    capture = diagnostics._capture_fixed_child(
        ("fixed",),
        merged=False,
        process_factory=lambda *args, **kwargs: process,
        monotonic=iter((0.0, 11.0)).__next__,
    )
    assert capture.state == "timeout"
    assert process.stdout.closed.is_set() and process.stderr.closed.is_set()


def test_cleanup_uses_kill_after_terminate_failure_and_cleanup_failure_is_unavailable():
    process = _FakeProcess(terminate_raises=True)
    capture = diagnostics._capture_fixed_child(
        ("fixed",),
        merged=False,
        process_factory=lambda *args, **kwargs: process,
        monotonic=iter((0.0, 11.0)).__next__,
    )
    assert capture.state == "timeout"
    assert process.terminated == 1 and process.killed == 1

    failed = _FakeProcess(terminate_raises=True, kill_raises=True)
    capture = diagnostics._capture_fixed_child(
        ("fixed",),
        merged=False,
        process_factory=lambda *args, **kwargs: failed,
        monotonic=iter((0.0, 11.0)).__next__,
    )
    assert capture.state == "unavailable"
    retained = diagnostics._capture_fixed_child(
        ("fixed",), merged=False, retain_stdout_on_error=True,
        process_factory=lambda *args, **kwargs: failed,
        monotonic=iter((0.0, 11.0)).__next__,
    )
    assert retained.stdout == retained.stderr == b""


def test_injected_factory_clock_and_reader_failures_are_fixed_non_ok():
    assert (
        diagnostics._capture_fixed_child(
            ("fixed",),
            merged=False,
            process_factory=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("raw")),
        ).state
        == "unavailable"
    )

    class ReadFailurePipe:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return b"partial-private-stdout"
            raise OSError("synthetic read failure")

        def close(self):
            pass

    process = _FakeProcess(exited=True)
    process.stdout = ReadFailurePipe()
    process.stderr = io.BytesIO()
    capture = diagnostics._capture_fixed_child(
        ("fixed",), merged=False, retain_stdout_on_error=True,
        process_factory=lambda *args, **kwargs: process,
    )
    assert capture.state == "malformed"
    assert capture.stdout == capture.stderr == b""


def test_hostile_post_spawn_proxies_are_fixed_and_reaped():
    class BadString(str):
        def __hash__(self):
            raise RuntimeError("raw")

    with pytest.raises(ValueError):
        diagnostics.safe_result(BadString("logs"), "ok")

    class HostileProcess(_FakeProcess):
        @property
        def stdout(self):
            raise RuntimeError("raw")

        @stdout.setter
        def stdout(self, value):
            self._stdout = value

        @property
        def stderr(self):
            return self._stderr

        @stderr.setter
        def stderr(self, value):
            self._stderr = value

        def poll(self):
            raise RuntimeError("raw")

    process = HostileProcess()
    capture = diagnostics._capture_fixed_child(
        ("fixed",), merged=False, process_factory=lambda *args, **kwargs: process
    )
    assert capture.state == "unavailable"
    assert process.terminated == 1 and process.killed == 1


def test_deadline_precedes_synchronous_eof_completion_and_thread_start_failure_reaps(monkeypatch):
    process = _FakeProcess(exited=True)
    process.stdout = io.BytesIO()
    process.stderr = io.BytesIO()
    capture = diagnostics._capture_fixed_child(
        ("fixed",),
        merged=False,
        process_factory=lambda *args, **kwargs: process,
        monotonic=iter((0.0, 11.0)).__next__,
    )
    assert capture.state == "timeout"

    class StartFailure:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("raw")

        def is_alive(self):
            return False

    process = _FakeProcess()
    monkeypatch.setattr(diagnostics.threading, "Thread", StartFailure)
    capture = diagnostics._capture_fixed_child(
        ("fixed",), merged=False, process_factory=lambda *args, **kwargs: process
    )
    assert capture.state == "unavailable" and process.terminated == 1
    process = _FakeProcess()
    assert (
        diagnostics._capture_fixed_child(
            ("fixed",),
            merged=False,
            process_factory=lambda *args, **kwargs: process,
            monotonic=lambda: (_ for _ in ()).throw(RuntimeError("raw")),
        ).state
        != "ok"
    )


_CONTAINER_ID = "a" * 64


def _inspect_document(**overrides):
    document = {
        "container_id": _CONTAINER_ID,
        "running": True,
        "exit_code": 0,
        "health": "healthy",
        "compose_service": "controller",
        "configured_bindings": {
            "8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "39077"}],
        },
        "observed_bindings": {"8000/tcp": None},
    }
    document.update(overrides)
    return document


def _capture_bytes(value, *, state="ok", truncated=False, stderr=b""):
    if not isinstance(value, bytes):
        value = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return diagnostics.ChildCapture(state, value, stderr, truncated)


class _CaptureSpy:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), dict(kwargs)))
        return self.results.pop(0)


def test_inspect_uses_fixed_template_and_keeps_configured_observed_separate():
    capture = _CaptureSpy(_capture_bytes(_inspect_document()))
    result = diagnostics.inspect_controller("controller-1", platform="linux", _capture=capture)

    assert result == {
        "schema_version": "controller-diagnostics/v1",
        "kind": "inspect",
        "state": "ok",
        "error_code": None,
        "container_id": _CONTAINER_ID,
        "truncated": False,
        "running": True,
        "exit_code": 0,
        "health": "healthy",
        "configured_bindings": [
            {"container_port": 8000, "host_port": 39077, "bind_class": "loopback"},
        ],
        "observed_bindings": [],
    }
    argv, kwargs = capture.calls[0]
    assert argv[:3] == ("docker", "--host", "unix:///var/run/docker.sock")
    assert argv[3:5] == ("inspect", "--format") and argv[-1] == "controller-1"
    template = argv[-2]
    assert all(value not in template for value in (".Config.Env", ".Config.Cmd", ".Mounts", ".State.Health.Log"))
    assert kwargs["merged"] is False


def test_inspect_argument_platform_capture_and_identity_failures_are_fixed():
    capture = _CaptureSpy(_capture_bytes(_inspect_document()))
    assert diagnostics.inspect_controller("-bad", platform="linux", _capture=capture)["state"] == "malformed"
    assert capture.calls == []
    assert diagnostics.inspect_controller("controller", platform="darwin", _capture=capture)["state"] == "unsupported"
    assert capture.calls == []

    failed = _CaptureSpy(diagnostics.ChildCapture("timeout", b"secret", b"raw", True))
    result = diagnostics.inspect_controller("controller", platform="linux", _capture=failed)
    assert result["state"] == "timeout" and result["truncated"] is True
    assert result["running"] is None and result["configured_bindings"] == []
    assert "secret" not in json.dumps(result)

    malformed = _inspect_document(container_id="A" * 64)
    result = diagnostics.inspect_controller(
        "controller", platform="linux", _capture=_CaptureSpy(_capture_bytes(malformed))
    )
    assert result["state"] == "malformed" and result["container_id"] is None


def test_binding_projection_uses_explicit_address_classes_and_never_returns_addresses():
    addresses = [
        "",
        "0.0.0.0",
        "127.2.3.4",
        "10.0.0.1",
        "100.64.0.1",
        "169.254.1.1",
        "192.0.2.1",
        "8.8.8.8",
        "::",
        "::1",
        "fc00::1",
        "2001:db8::1",
        "2606:4700:4700::1111",
        "::ffff:8.8.8.8",
    ]
    bindings = [
        {"HostIp": address, "HostPort": str(30000 + index)}
        for index, address in enumerate(addresses)
    ]
    document = _inspect_document(
        configured_bindings={"8000/tcp": bindings},
        observed_bindings={},
    )
    result = diagnostics.inspect_controller(
        "controller", platform="linux", _capture=_CaptureSpy(_capture_bytes(document))
    )
    assert [row["bind_class"] for row in result["configured_bindings"]] == [
        "wildcard",
        "wildcard",
        "loopback",
        "private",
        "private",
        "unknown",
        "unknown",
        "public",
        "wildcard",
        "loopback",
        "private",
        "unknown",
        "public",
        "unknown",
    ]
    rendered = json.dumps(result)
    assert all(address not in rendered for address in addresses if address)


def test_inspect_rejects_bad_shapes_ports_and_binding_overflow_without_partial_rows():
    bad_documents = [
        {**_inspect_document(), "unexpected": "secret"},
        _inspect_document(running=1),
        _inspect_document(exit_code=True),
        _inspect_document(health="unknown"),
        _inspect_document(configured_bindings={"8000/udp": []}),
        _inspect_document(
            configured_bindings={"8000/tcp": [{"HostIp": "bad host", "HostPort": "1"}]}
        ),
        _inspect_document(
            configured_bindings={"8000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]}
        ),
    ]
    for document in bad_documents:
        result = diagnostics.inspect_controller(
            "controller", platform="linux", _capture=_CaptureSpy(_capture_bytes(document))
        )
        assert result["state"] == "malformed"
        assert result["configured_bindings"] == result["observed_bindings"] == []

    too_many = _inspect_document(
        configured_bindings={
            "8000/tcp": [
                {"HostIp": "127.0.0.1", "HostPort": str(1000 + index)}
                for index in range(65)
            ]
        }
    )
    result = diagnostics.inspect_controller(
        "controller", platform="linux", _capture=_CaptureSpy(_capture_bytes(too_many))
    )
    assert result["state"] == "output-limit" and result["truncated"] is True
    assert result["configured_bindings"] == []


def test_logs_requires_controller_label_then_uses_only_immutable_id():
    unsupported = _CaptureSpy(_capture_bytes(_inspect_document(compose_service="other")))
    result = diagnostics.controller_logs("mutable-name", platform="linux", _capture=unsupported)
    assert result["state"] == "unsupported" and len(unsupported.calls) == 1

    capture = _CaptureSpy(
        _capture_bytes(_inspect_document()),
        _capture_bytes(b'{"operation":"health","status":200}\n'),
    )
    result = diagnostics.controller_logs(
        "mutable-name", 17, platform="linux", _capture=capture
    )
    assert result["state"] == "ok" and result["returned_events"] == 1
    log_argv, log_kwargs = capture.calls[1]
    assert log_argv == (
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
        "logs",
        "--tail",
        "17",
        _CONTAINER_ID,
    )
    assert "mutable-name" not in log_argv and log_kwargs["merged"] is True


def test_logs_project_allowlist_counts_unknowns_and_rejects_hostile_lines():
    oversized = b"x" * (16 * 1024 + 1)
    lines = [
        b'{"operation":"health","status":200,"elapsed_ms":1.5,"secret":"credential-value"}',
        b'{"operation":"evil","event":"evil","error_code":"evil"}',
        b'{"operation":"health","status":true,"ignored":"secret-2"}',
        b'{"operation":"health","status":200,"status":201}',
        b'{"operation":"health","elapsed_ms":NaN}',
        b'{"error_code":"internal_error"}',
        b'\xff',
        oversized,
        b'{"event":"audit_file_write_failed","error_code":"internal_error"}',
    ]
    raw = b"\n".join(lines)
    capture = _CaptureSpy(_capture_bytes(_inspect_document()), _capture_bytes(raw))
    result = diagnostics.controller_logs("controller", platform="linux", _capture=capture)

    assert result["state"] == "ok"
    assert result["line_count"] == 9
    assert result["returned_events"] == 2
    assert result["rejected_lines"] == 7
    assert result["unknown_fields"] == 2
    assert result["unknown_codes"] == 3
    assert result["events"] == [
        {"operation": "health", "status": 200, "elapsed_ms": 1.5},
        {"event": "audit_file_write_failed", "error_code": "internal_error"},
    ]
    rendered = json.dumps(result)
    assert "credential-value" not in rendered and "secret" not in rendered and "evil" not in rendered


def test_logs_caps_events_preserves_final_partial_and_clears_capture_failures():
    raw = b"\n".join(b'{"operation":"mcp"}' for _ in range(201))
    capture = _CaptureSpy(_capture_bytes(_inspect_document()), _capture_bytes(raw))
    result = diagnostics.controller_logs("controller", platform="linux", _capture=capture)
    assert result["line_count"] == 201 and result["returned_events"] == 200
    assert result["truncated"] is True and result["counters_saturated"] is False

    failure = _CaptureSpy(
        _capture_bytes(_inspect_document()),
        diagnostics.ChildCapture("output-limit", b"partial-secret", b"", True),
    )
    result = diagnostics.controller_logs("controller", platform="linux", _capture=failure)
    assert result["state"] == "output-limit" and result["events"] == []
    assert result["line_count"] == result["unknown_fields"] == result["unknown_codes"] == 0
    assert "partial-secret" not in json.dumps(result)


def test_logs_invalid_arguments_execute_no_child_and_all_rejected_or_empty_remain_ok():
    capture = _CaptureSpy(_capture_bytes(_inspect_document()))
    for container, tail in (("-bad", 100), ("controller", True), ("controller", 201)):
        result = diagnostics.controller_logs(container, tail, platform="linux", _capture=capture)
        assert result["state"] == "malformed"
    assert capture.calls == []

    for raw, expected_lines in ((b"", 0), (b"not-json", 1)):
        calls = _CaptureSpy(_capture_bytes(_inspect_document()), _capture_bytes(raw))
        result = diagnostics.controller_logs("controller", platform="linux", _capture=calls)
        assert result["state"] == "ok" and result["line_count"] == expected_lines
        assert result["returned_events"] == 0


def test_log_counters_saturate_without_changing_safe_event_projection(monkeypatch):
    monkeypatch.setattr(diagnostics, "_MAX_COUNTER", 1)
    raw = (
        b'{"operation":"health","first":"secret-1"}\n'
        b'{"operation":"health","second":"secret-2"}'
    )
    capture = _CaptureSpy(_capture_bytes(_inspect_document()), _capture_bytes(raw))
    result = diagnostics.controller_logs("controller", platform="linux", _capture=capture)
    assert result["state"] == "ok" and result["returned_events"] == 2
    assert result["line_count"] == result["unknown_fields"] == 1
    assert result["counters_saturated"] is True
    assert "secret" not in json.dumps(result)


def test_cli_run_is_exact_and_invalid_arguments_never_call_diagnostics(monkeypatch):
    calls = []
    inspect_result = diagnostics.safe_result("inspect", "ok")
    logs_result = diagnostics.safe_result("logs", "unavailable")
    monkeypatch.setattr(
        diagnostics,
        "inspect_controller",
        lambda container: calls.append(("inspect", container)) or inspect_result,
    )
    monkeypatch.setattr(
        diagnostics,
        "controller_logs",
        lambda container, tail: calls.append(("logs", container, tail)) or logs_result,
    )

    assert diagnostics.run(["inspect", "--container", "controller_1"]) == inspect_result
    assert diagnostics.run(["logs", "--container", "controller_1", "--tail", "17"]) == logs_result
    assert calls == [("inspect", "controller_1"), ("logs", "controller_1", 17)]

    for argv in (
        ["inspect"],
        ["inspect", "--container", "bad/name"],
        ["inspect", "--cont", "controller_1"],
        ["logs", "--container", "controller_1", "--tail", "0"],
        ["logs", "--container", "controller_1", "--tail", "201"],
        ["logs", "--container", "controller_1", "--tail", "true"],
        ["logs", "--container", "controller_1", "--tail", "1.0"],
        ["logs", "--container", "controller_1", "--tail", "+1"],
        ["logs", "--container", "controller_1", "--tail", "1_0"],
        ["logs", "--container", "controller_1", "--tail", " 1"],
        ["logs", "--container", "controller_1", "--tail", "\u0661"],
        ["logs", "--container", "controller_1", "--follow"],
    ):
        with pytest.raises(SystemExit) as exc:
            diagnostics.run(argv)
        assert exc.value.code == 2
    assert calls == [("inspect", "controller_1"), ("logs", "controller_1", 17)]


def test_cli_parser_uses_public_controller_identity_for_help_and_errors(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        diagnostics,
        "inspect_controller",
        lambda container: calls.append(container) or diagnostics.safe_result("inspect", "ok"),
    )

    assert diagnostics.main(["inspect", "--help"]) == 0
    help_output = capsys.readouterr().out
    assert "usage: anvil-serving controller inspect" in help_output
    assert "--container" in help_output

    assert diagnostics.main(["inspect"]) == 2
    error_output = capsys.readouterr().err
    assert "usage: anvil-serving controller inspect" in error_output
    assert calls == []


def test_cli_main_prints_only_safe_json_and_has_fixed_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(diagnostics, "run", lambda argv: diagnostics.safe_result("inspect", "ok"))
    assert diagnostics.main(["inspect", "--container", "controller"]) == 0
    assert json.loads(capsys.readouterr().out) == diagnostics.safe_result("inspect", "ok")

    monkeypatch.setattr(diagnostics, "run", lambda argv: diagnostics.safe_result("logs", "timeout"))
    assert diagnostics.main(["logs", "--container", "controller"]) == 1
    assert json.loads(capsys.readouterr().out) == diagnostics.safe_result("logs", "timeout")

    monkeypatch.setattr(diagnostics, "run", lambda argv: (_ for _ in ()).throw(SystemExit(2)))
    assert diagnostics.main(["inspect"]) == 2
    assert capsys.readouterr().out == ""


def test_controller_diagnostic_command_nodes_are_local_read_only_and_bounded():
    controller = next(node for node in control_plane_commands.build() if node.name == "controller")
    nodes = {node.name: node for node in controller.children}
    for name, prefix, tool, allowed in (
        ("inspect", ("inspect",), "controller_inspect", ("container",)),
        ("logs", ("logs",), "controller_logs", ("container", "tail")),
    ):
        node = nodes[name]
        assert node.handler is not None
        assert node.handler.module == "anvil_serving.controller_diagnostics"
        assert node.handler.argv_prefix == prefix
        assert node.resource_role == "controller"
        assert node.transports == ("local", "controller")
        assert node.execution_runtime_roles == ("native", "docker")
        assert node.gpu_role_required is False
        assert node.mutation_class == "read"
        assert node.remote_operation is not None
        assert node.remote_operation.tool == tool
        assert node.remote_operation.allowed_arguments == allowed
    inspect_flags = {flag for option in nodes["inspect"].options for flag in option.flags}
    logs_flags = {flag for option in nodes["logs"].options for flag in option.flags}
    assert inspect_flags == {"--container"}
    assert logs_flags == {"--container", "--tail"}


def test_controller_command_tree_dispatches_to_fixed_diagnostic_handler(monkeypatch, capsys):
    calls = []
    inspect_result = {
        **diagnostics.safe_result("inspect", "ok", container_id="a" * 64),
        "running": True,
        "exit_code": 0,
        "health": "healthy",
        "configured_bindings": [],
        "observed_bindings": [],
    }
    monkeypatch.setattr(cli, "_resolve_dispatch_plan", lambda path, options: None)
    monkeypatch.setattr(
        diagnostics,
        "inspect_controller",
        lambda container: calls.append(container) or inspect_result,
    )

    assert cli.main(["controller", "inspect", "--container", "controller_1"]) == 0
    assert calls == ["controller_1"]
    assert json.loads(capsys.readouterr().out) == inspect_result


def test_mcp_diagnostics_match_cli_library_results_and_discovery(monkeypatch):
    calls = []
    inspect_result = diagnostics.safe_result("inspect", "ok")
    logs_result = diagnostics.safe_result("logs", "unavailable")
    monkeypatch.setattr(
        diagnostics,
        "inspect_controller",
        lambda container: calls.append(("inspect", container)) or inspect_result,
    )
    monkeypatch.setattr(
        diagnostics,
        "controller_logs",
        lambda container, tail: calls.append(("logs", container, tail)) or logs_result,
    )

    assert diagnostics.run(["inspect", "--container", "controller_1"]) == inspect_result
    assert mcp.call_tool("controller_inspect", {"container": "controller_1"}) == {
        "ok": True,
        "data": inspect_result,
    }
    assert diagnostics.run(["logs", "--container", "controller_1", "--tail", "17"]) == logs_result
    assert mcp.call_tool("controller_logs", {"container": "controller_1", "tail": 17}) == {
        "ok": True,
        "data": logs_result,
    }
    assert calls == [
        ("inspect", "controller_1"),
        ("inspect", "controller_1"),
        ("logs", "controller_1", 17),
        ("logs", "controller_1", 17),
    ]

    tools = {tool["name"]: tool for tool in mcp.list_tools()}
    assert {"controller_inspect", "controller_logs"} <= set(tools)
    assert tools["controller_inspect"]["inputSchema"]["required"] == ["container"]
    assert tools["controller_logs"]["inputSchema"]["properties"]["tail"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
        "default": 100,
    }
    operations = {entry["name"]: entry for entry in mcp.operation_declarations()}
    assert operations["controller-inspect"]["tool"] == "controller_inspect"
    assert operations["controller-logs"]["tool"] == "controller_logs"


def test_mcp_diagnostics_reject_invalid_inputs_before_library_calls(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "inspect_controller",
        lambda container: (_ for _ in ()).throw(AssertionError("inspect called")),
    )
    monkeypatch.setattr(
        diagnostics,
        "controller_logs",
        lambda container, tail: (_ for _ in ()).throw(AssertionError("logs called")),
    )

    for name, arguments in (
        ("controller_inspect", {}),
        ("controller_inspect", {"container": "bad/name"}),
        ("controller_inspect", {"container": "controller", "extra": 1}),
        ("controller_logs", {"container": "controller", "tail": True}),
        ("controller_logs", {"container": "controller", "tail": 1.0}),
        ("controller_logs", {"container": "controller", "tail": "1"}),
        ("controller_logs", {"container": "controller", "tail": 0}),
        ("controller_logs", {"container": "controller", "tail": 201}),
        ("controller_logs", {"container": "controller", "unknown": 1}),
    ):
        result = mcp.call_tool(name, arguments)
        assert result["ok"] is False
        assert result["error"]["code"] in {"bad_argument", "missing_argument"}

    for handler, arguments in (
        (operations._controller_inspect, {"container": "bad/name"}),
        (operations._controller_inspect, {"container": "controller", "extra": 1}),
        (operations._controller_logs, {"container": "controller", "tail": True}),
        (operations._controller_logs, {"container": "controller", "tail": 201}),
    ):
        with pytest.raises(ToolError) as error:
            handler(arguments)
        assert error.value.code == "bad_argument"
        assert "bad/name" not in error.value.message
