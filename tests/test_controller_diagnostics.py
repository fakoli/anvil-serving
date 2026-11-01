"""Hermetic bounded-child coverage; no Docker daemon is invoked."""

import sys
import threading
import io
import json

import pytest

from anvil_serving import controller_diagnostics as diagnostics, mcp
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import operations


def _child(source):
    return (sys.executable, "-c", source)


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
