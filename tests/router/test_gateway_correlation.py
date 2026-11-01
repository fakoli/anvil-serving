"""Trusted gateway request correlation across front door and relay."""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message
from tests.router.helpers import StaticBackend, make_tier


_TOKEN = "front-door-test-token"
_GATEWAY_ID_RE = re.compile(r"req_[0-9a-f]{32}\Z")
_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


class CaptureBackend(StaticBackend):
    def __init__(self, *, failure: Exception | None = None):
        super().__init__(["ok"])
        self.requests: list[InternalRequest] = []
        self.failure = failure

    def generate(self, request: InternalRequest):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        return iter(["ok"])


class PurposeStub:
    def dispatch(self, kind, body, *, correlation=None):
        return {"object": "list", "data": [], "model": body["model"]}


class AudioStub:
    paths = frozenset({"/v1/audio/speech"})
    max_request_body_bytes = 1024 * 1024

    def __init__(self):
        self.correlation = None

    def has_purpose(self, kind):
        return kind == "tts"

    def acquire(self):
        return True

    def release(self):
        return None

    def dispatch_speech(self, body, *, correlation):
        self.correlation = correlation
        return {"audio": "b2s="}


@contextmanager
def _server(backend, *, purpose=None, audio=None):
    server = make_server(
        "127.0.0.1",
        0,
        backend,
        model_routes=("llm.primary",),
        auth_token=_TOKEN,
        purpose=purpose,
        audio=audio,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def _raw_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(host, port, path, body, *, headers=None):
    request_headers = {
        "Authorization": f"Bearer {_TOKEN}",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("POST", path, json.dumps(body), request_headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_keepalive_resets_ids_across_health_and_auth_boundaries():
    with _server(CaptureBackend()) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        body = json.dumps(
            {"model": "llm.primary", "messages": [{"role": "user", "content": "hi"}]}
        )
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                body,
                {
                    "Authorization": f"Bearer {_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            first = connection.getresponse()
            first_headers = dict(first.getheaders())
            first.read()

            connection.request("GET", "/health", headers={"Authorization": f"Bearer {_TOKEN}"})
            health = connection.getresponse()
            health_headers = dict(health.getheaders())
            health.read()

            connection.request(
                "POST",
                "/v1/chat/completions",
                body,
                {"Content-Type": "application/json"},
            )
            denied = connection.getresponse()
            denied_headers = dict(denied.getheaders())
            denied.read()
        finally:
            connection.close()

    assert _GATEWAY_ID_RE.fullmatch(first_headers["X-Anvil-Request-Id"])
    assert "X-Anvil-Request-Id" not in health_headers
    assert "X-Request-Id" not in health_headers
    assert denied.status == 401
    assert "X-Anvil-Request-Id" not in denied_headers
    assert "X-Request-Id" not in denied_headers


class _StreamResponse(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


@pytest.mark.parametrize("stream", [False, True])
def test_default_relay_denies_redirect_without_forwarding_authorization(stream):
    receiver_authorization = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            receiver_authorization.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    with _raw_server(Receiver) as (receiver_host, receiver_port):
        target = f"http://{receiver_host}:{receiver_port}/capture"

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                self.send_response(307)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        with _raw_server(Redirect) as (redirect_host, redirect_port):
            tier = replace(
                make_tier("openai"),
                base_url=f"http://{redirect_host}:{redirect_port}",
            )
            backend = RelayBackend(
                tier, env={"EXAMPLE_KEY": "upstream-tier-secret"}, timeout=2
            )
            request = InternalRequest(
                model="llm.primary",
                messages=[Message("user", "hi")],
                stream=stream,
                dialect="openai",
                raw={
                    "_anvil_correlation": {
                        "gateway_request_id": "req_0123456789abcdef0123456789abcdef"
                    }
                },
            )
            with pytest.raises(RelayBackendError, match=r"HTTP 307"):
                list(backend.generate(request))

    assert receiver_authorization == []
