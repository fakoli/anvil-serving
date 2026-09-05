"""Trusted gateway request correlation across front door and relay."""
from __future__ import annotations

import http.client
import io
import json
import re
import threading
import urllib.error
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anvil_serving.router.backends import relay as relay_module
from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.config import load
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.serve import RoutingBackend
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/chat/completions",
            {"model": "llm.primary", "messages": [{"role": "user", "content": "hi"}]},
        ),
        ("/v1/responses", {"model": "llm.primary", "input": "hi"}),
        (
            "/v1/messages",
            {
                "model": "llm.primary",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "hi"}],
            },
        ),
    ],
)
@pytest.mark.parametrize("stream", [False, True])
def test_all_chat_dialects_return_trusted_ids_for_buffered_and_streaming(
    path, body, stream
):
    backend = CaptureBackend()
    with _server(backend) as (host, port):
        status, headers, _ = _post(
            host,
            port,
            path,
            {**body, "stream": stream},
            headers={
                "X-Request-Id": "caller-request-7",
                "X-Anvil-Request-Id": "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
        )

    assert status == 200
    assert _GATEWAY_ID_RE.fullmatch(headers["X-Anvil-Request-Id"])
    assert headers["X-Anvil-Request-Id"] != "req_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert headers["X-Request-Id"] == "caller-request-7"
    stamped = backend.requests[0].raw["_anvil_correlation"]
    assert stamped["gateway_request_id"] == headers["X-Anvil-Request-Id"]
    assert stamped["request_id"] == "caller-request-7"


def test_body_lineage_is_overwritten_and_invalid_legacy_id_defaults_to_gateway_id():
    backend = CaptureBackend()
    body = {
        "model": "llm.primary",
        "messages": [{"role": "user", "content": "hi"}],
        "_anvil_correlation": {
            "gateway_request_id": "req_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "request_id": "body-request",
            "workbench_run_id": "body-run",
        },
    }
    with _server(backend) as (host, port):
        status, headers, _ = _post(
            host,
            port,
            "/v1/chat/completions",
            body,
            headers={
                "X-Request-Id": "invalid id with spaces",
                "X-Anvil-Request-Id": "req_cccccccccccccccccccccccccccccccc",
            },
        )

    assert status == 200
    gateway_id = headers["X-Anvil-Request-Id"]
    assert headers["X-Request-Id"] == gateway_id
    assert backend.requests[0].raw["_anvil_correlation"] == {
        "gateway_request_id": gateway_id,
        "request_id": gateway_id,
    }


def test_purpose_and_audio_requests_return_ids_and_audio_gets_trusted_lineage():
    audio = AudioStub()
    with _server(
        CaptureBackend(), purpose=PurposeStub(), audio=audio
    ) as (host, port):
        purpose_status, purpose_headers, _ = _post(
            host,
            port,
            "/v1/embeddings",
            {"model": "embed-model", "input": "hi"},
        )
        audio_status, audio_headers, _ = _post(
            host,
            port,
            "/v1/audio/speech",
            {"model": "tts-model", "input": "hi"},
        )

    assert purpose_status == audio_status == 200
    assert _GATEWAY_ID_RE.fullmatch(purpose_headers["X-Anvil-Request-Id"])
    assert _GATEWAY_ID_RE.fullmatch(audio_headers["X-Anvil-Request-Id"])
    assert purpose_headers["X-Anvil-Request-Id"] != audio_headers["X-Anvil-Request-Id"]
    assert audio.correlation["gateway_request_id"] == audio_headers["X-Anvil-Request-Id"]


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


def test_duplicate_caller_ids_get_distinct_gateway_ids_and_exact_lookup():
    config = load(_CONFIG)
    routing = RoutingBackend(
        config,
        {
            "primary-local": StaticBackend(["ok"]),
            "omni-local": StaticBackend(["ok"]),
        },
    )
    with _server(routing) as (host, port):
        ids = []
        for _ in range(2):
            status, headers, _ = _post(
                host,
                port,
                "/v1/chat/completions",
                {
                    "model": "llm.primary",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"X-Request-Id": "caller-duplicate"},
            )
            assert status == 200
            ids.append(headers["X-Anvil-Request-Id"])

        assert ids[0] != ids[1]
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            for gateway_id in ids:
                connection.request(
                    "GET",
                    f"/v1/requests/{gateway_id}",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                assert response.status == 200
                assert payload["record"]["gateway_request_id"] == gateway_id
                assert payload["record"]["request_id"] == "caller-duplicate"
        finally:
            connection.close()


class _StreamResponse(io.BytesIO):
    headers = {"Content-Type": "text/event-stream"}


@pytest.mark.parametrize("stream", [False, True])
def test_relay_forwards_only_valid_generated_id(stream):
    calls = []

    def buffered(url, *, data, headers, timeout):
        calls.append((json.loads(data), dict(headers)))
        return b'{"choices":[{"message":{"content":"ok"}}]}'

    def streaming(url, *, data, headers, timeout):
        calls.append((json.loads(data), dict(headers)))
        return _StreamResponse(
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

    backend = RelayBackend(
        make_tier("openai"),
        env={"EXAMPLE_KEY": "upstream-tier-secret"},
        transport=buffered,
        stream_transport=streaming if stream else None,
    )
    gateway_id = "req_0123456789abcdef0123456789abcdef"
    request = InternalRequest(
        model="llm.primary",
        messages=[Message("user", "hi")],
        stream=stream,
        dialect="openai",
        raw={
            "messages": [{"role": "user", "content": "hi"}],
            "_anvil_correlation": {
                "gateway_request_id": gateway_id,
                "request_id": "caller-id",
                "workbench_run_id": "workbench-id",
                "task_id": "task-id",
            },
            "Authorization": "Bearer caller-secret",
        },
    )

    assert list(backend.generate(request)) == ["ok"]
    upstream_body, upstream_headers = calls[0]
    assert upstream_headers == {
        "Content-Type": "application/json",
        "Authorization": "Bearer upstream-tier-secret",
        "X-Request-Id": gateway_id,
    }
    assert "_anvil_correlation" not in upstream_body


def test_relay_discards_untrusted_gateway_id():
    calls = []

    def buffered(url, *, data, headers, timeout):
        calls.append(dict(headers))
        return b'{"choices":[{"message":{"content":"ok"}}]}'

    backend = RelayBackend(
        make_tier("openai"), env={}, transport=buffered
    )
    request = InternalRequest(
        model="llm.primary",
        messages=[Message("user", "hi")],
        dialect="openai",
        raw={"_anvil_correlation": {"gateway_request_id": "caller-controlled"}},
    )
    assert list(backend.generate(request)) == ["ok"]
    assert "X-Request-Id" not in calls[0]


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


def test_http_error_reason_is_not_logged_or_raised_and_response_is_closed(
    monkeypatch, capsys
):
    upstream_error = urllib.error.HTTPError(
        "http://127.0.0.1:30000/v1/chat/completions",
        502,
        "hostile secret status reason",
        {},
        io.BytesIO(b"hostile secret response body"),
    )

    def fail_open(request, timeout):
        raise upstream_error

    monkeypatch.setattr(relay_module, "_direct_open", fail_open)
    headers = {
        "X-Request-Id": "req_0123456789abcdef0123456789abcdef"
    }
    with pytest.raises(RelayBackendError) as raised:
        relay_module._urlopen_transport(
            "http://127.0.0.1:30000/v1/chat/completions",
            data=b"{}",
            headers=headers,
            timeout=1,
        )

    logged = capsys.readouterr().err
    assert "HTTP 502" in logged
    assert headers["X-Request-Id"] in logged
    assert "hostile secret" not in logged
    assert "hostile secret" not in str(raised.value)
    assert upstream_error.fp is None or upstream_error.fp.closed


def test_url_error_detail_is_not_logged_or_raised(monkeypatch, capsys):
    def fail_open(request, timeout):
        raise urllib.error.URLError("hostile secret transport detail")

    monkeypatch.setattr(relay_module, "_direct_open", fail_open)
    headers = {
        "X-Request-Id": "req_0123456789abcdef0123456789abcdef"
    }
    with pytest.raises(RelayBackendError, match="model upstream request failed") as raised:
        relay_module._urlopen_stream_transport(
            "http://127.0.0.1:30000/v1/chat/completions",
            data=b"{}",
            headers=headers,
            timeout=1,
        )

    logged = capsys.readouterr().err
    assert "str" in logged
    assert headers["X-Request-Id"] in logged
    assert "hostile secret" not in logged
    assert "hostile secret" not in str(raised.value)


def test_backend_failure_log_has_class_and_gateway_id_without_exception_text(capsys):
    backend = CaptureBackend(failure=RuntimeError("raw upstream secret body"))
    with _server(backend) as (host, port):
        status, headers, _ = _post(
            host,
            port,
            "/v1/chat/completions",
            {"model": "llm.primary", "messages": [{"role": "user", "content": "hi"}]},
        )

    captured = capsys.readouterr().err
    assert status == 500
    assert "RuntimeError" in captured
    assert headers["X-Anvil-Request-Id"] in captured
    assert "raw upstream secret body" not in captured
