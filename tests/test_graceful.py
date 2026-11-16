"""Tests for bounded graceful shutdown (ADR-0033)."""

import threading
import time
from http.server import BaseHTTPRequestHandler


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
