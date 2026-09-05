"""Hermetic bounded-child coverage; no Docker daemon is invoked."""

import os
import sys
import threading
import io

import pytest

from anvil_serving import controller_diagnostics as diagnostics


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
        _child("import time;time.sleep(1)"), merged=False, monotonic=iter((0.0, 11.0)).__next__
    )
    assert timeout.state == "timeout" and timeout.truncated is True
    assert (
        diagnostics._capture_fixed_child(_child("import sys;sys.exit(3)"), merged=False).state
        == "unavailable"
    )


def test_exact_default_capture_ceiling_and_one_byte_overflow():
    exact = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('x'*262144)"), merged=False
    )
    assert exact.state == "ok" and len(exact.stdout) == diagnostics.MAX_CAPTURE_BYTES
    over = diagnostics._capture_fixed_child(
        _child("import sys;sys.stdout.write('x'*262145)"), merged=False
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


def test_injected_factory_clock_and_reader_failures_are_fixed_non_ok():
    assert (
        diagnostics._capture_fixed_child(
            ("fixed",),
            merged=False,
            process_factory=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("raw")),
        ).state
        == "unavailable"
    )


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
