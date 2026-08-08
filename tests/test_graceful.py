"""Tests for bounded graceful shutdown (ADR-0033)."""

import http.client
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving import graceful


class _SlowHandler(BaseHTTPRequestHandler):
    hold_seconds = 5.0

    def do_GET(self):  # noqa: N802 - stdlib handler naming
        started = threading.Event()
        self.server.anvil_request_started = started
        started.set()
        time.sleep(self.hold_seconds)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def test_drain_returns_early_when_threads_finish():
    thread = threading.Thread(target=lambda: time.sleep(0.1), daemon=True)
    thread.start()
    waited, left = graceful._drain([thread], time.monotonic() + 5.0)
    assert left == 0
    assert waited < 4.0


def test_drain_respects_deadline_with_stuck_thread():
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        waited, left = graceful._drain([thread], time.monotonic() + 0.3)
        assert time.monotonic() - started < 2.0
        assert left == 1
    finally:
        stop.set()
        thread.join(timeout=2)


def test_drain_skips_current_thread():
    waited, left = graceful._drain([threading.current_thread()], time.monotonic() + 0.5)
    assert left == 0


def test_serve_until_signal_handler_stops_server_and_bounds_drain(monkeypatch, capsys):
    real_signal = signal.signal
    captured = {}

    def capture(signum, handler):
        captured[signum] = handler
        return real_signal(signum, handler)

    monkeypatch.setattr(signal, "signal", capture)
    _SlowHandler.hold_seconds = 5.0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    host, port = httpd.server_address[:2]

    def client():
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            conn.request("GET", "/")
            conn.getresponse().read()
        except OSError:
            pass
        finally:
            conn.close()

    shutdown_calls = []

    def fire_term():
        threading.Thread(target=client, daemon=True).start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if getattr(httpd, "anvil_request_started", None) is not None:
                break
            time.sleep(0.02)
        handler = captured.get(signal.SIGTERM)
        assert handler is not None
        handler(signal.SIGTERM, None)

    trigger = threading.Thread(target=fire_term, daemon=True)
    trigger.start()
    started = time.monotonic()
    graceful.serve_until_signal(
        httpd, drain_seconds=0.5, on_shutdown=lambda: shutdown_calls.append(True)
    )
    elapsed = time.monotonic() - started
    trigger.join(timeout=5)

    # Stopped promptly, drained no longer than the budget plus overhead, and
    # the stuck in-flight request was reported rather than waited out.
    assert elapsed < 4.0
    assert shutdown_calls == [True]
    err = capsys.readouterr().err
    assert "shutdown drain:" in err
    assert "remaining_threads=1" in err


def test_serve_until_signal_off_main_thread_skips_handlers(monkeypatch):
    def refuse(signum, handler):  # signal.signal off-main raises ValueError
        raise AssertionError("signal.signal must not be called off the main thread")

    monkeypatch.setattr(signal, "signal", refuse)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    result = {}

    def run():
        try:
            threading.Timer(0.2, httpd.shutdown).start()
            graceful.serve_until_signal(httpd, drain_seconds=0.2)
            result["ok"] = True
        except BaseException as exc:  # pragma: no cover - failure detail
            result["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert result.get("ok") is True, result.get("error")


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM delivery is a POSIX path")
def test_controller_subprocess_exits_cleanly_on_sigterm(tmp_path):
    import os
    import subprocess

    env = dict(os.environ)
    env["ANVIL_CONTROLLER_TOKEN"] = "test-token"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "anvil_serving.controller",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--state-db",
            str(tmp_path / "operations.sqlite3"),
            "--drain-seconds",
            "1",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        time.sleep(2.0)
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0
    assert b"shutdown drain:" in stderr
