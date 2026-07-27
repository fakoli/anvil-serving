"""HTTP contract for the authenticated direct capability gateway."""
from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.router.helpers import StaticBackend
from anvil_serving.router.config import load
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionRecord,
)
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import NoAvailableTierError
from anvil_serving.router.serve import RoutingBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"
_TOKEN = "test-gateway-token"


@contextmanager
def running_server(*, auth=False):
    config = load(_CONFIG)
    routing = RoutingBackend(config, {
        "primary-local": StaticBackend(["heavy response"]),
        "auxiliary-local": StaticBackend(["fast response"]),
    })
    server = make_server(
        "127.0.0.1", 0, routing, model_routes=config.model_routes,
        auth_token=_TOKEN if auth else None,
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
def running_backend(backend, *, auth=False):
    server = make_server(
        "127.0.0.1",
        0,
        backend,
        model_routes=("llm.primary",),
        auth_token=_TOKEN if auth else None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(host, port, method, path, body=None, *, token=None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _raw_roundtrip(host, port, request, *, timeout=3.0):
    chunks = []
    hit_eof = False
    with socket.create_connection((host, port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(request)
        try:
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    hit_eof = True
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
    return b"".join(chunks), hit_eof


def test_discovery_advertises_only_configured_aliases():
    with running_server() as (host, port):
        status, _, raw = _request(host, port, "GET", "/v1/models")

    assert status == 200
    assert [item["id"] for item in json.loads(raw)["data"]] == [
        "llm.primary",
        "llm.voice",
        "vision.ocr",
        "vision.general",
    ]


def test_unknown_alias_returns_clean_404_without_tier_identity():
    with running_server() as (host, port):
        status, _, raw = _request(host, port, "POST", "/v1/chat/completions", {
            "model": "missing", "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 404
    assert json.loads(raw)["error"]["type"] == "model_not_found"
    assert "primary-local" not in raw.decode()


def test_openai_streaming_relays_sse_for_exact_alias():
    with running_server() as (host, port):
        status, headers, raw = _request(host, port, "POST", "/v1/chat/completions", {
            "model": "llm.primary", "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 200
    assert headers["Content-Type"].startswith("text/event-stream")
    assert b"heavy response" in raw
    assert raw.rstrip().endswith(b"data: [DONE]")


def test_anthropic_non_streaming_preserves_native_envelope():
    with running_server() as (host, port):
        status, _, raw = _request(host, port, "POST", "/v1/messages", {
            "model": "llm.voice", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}],
        })

    payload = json.loads(raw)
    assert status == 200
    assert payload["type"] == "message"
    assert payload["content"][0]["text"] == "fast response"


def test_one_token_gates_models_and_chat_but_not_health():
    with running_server(auth=True) as (host, port):
        assert _request(host, port, "GET", "/healthz")[0] == 200
        assert _request(host, port, "GET", "/v1/models")[0] == 401
        assert _request(host, port, "GET", "/v1/models", token=_TOKEN)[0] == 200
        assert _request(host, port, "POST", "/v1/chat/completions", {
            "model": "llm.primary", "messages": [{"role": "user", "content": "hi"}],
        })[0] == 401
        assert _request(host, port, "POST", "/v1/chat/completions", {
            "model": "llm.primary", "messages": [{"role": "user", "content": "hi"}],
        }, token=_TOKEN)[0] == 200


def test_legacy_route_endpoint_is_not_exposed():
    with running_server() as (host, port):
        status, _, raw = _request(host, port, "POST", "/v1/route", {
            "model": "llm.primary", "messages": [{"role": "user", "content": "hi"}],
        })
        health_status, _, health_raw = _request(host, port, "GET", "/healthz")

    assert status == 404
    assert json.loads(raw)["error"]["type"] == "not_found"
    assert "/v1/route" not in json.loads(health_raw)["routes"]
    assert health_status == 200


def test_header_write_failure_closes_unadvanced_backend_iterator():
    closed = threading.Event()

    class CloseAware:
        def __iter__(self):
            return self

        def __next__(self):
            return "never reached"

        def close(self):
            closed.set()

    class Backend:
        def generate(self, request):
            return CloseAware()

    server = make_server(
        "127.0.0.1", 0, Backend(), model_routes=("llm.primary",)
    )
    handler = server.RequestHandlerClass

    def fail_headers(self, *args, **kwargs):
        raise ConnectionResetError("simulated disconnect before headers")

    handler.send_response = fail_headers
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request(
            "POST",
            "/v1/chat/completions",
            json.dumps({
                "model": "llm.primary",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            }),
            {"Content-Type": "application/json"},
        )
        with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError)):
            connection.getresponse()
        assert closed.wait(1)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("bad_length", ["1_000", "+5", " 5 "])
def test_non_digit_content_length_is_rejected(bad_length):
    with running_backend(StaticBackend(["x"])) as (host, port):
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {bad_length}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + b'{"model":"llm.primary","messages":[]}'
        )
        raw, _ = _raw_roundtrip(host, port, request)

    assert b" 400 " in raw.split(b"\r\n", 1)[0]


def test_duplicate_content_length_is_rejected():
    body = b'{"model":"llm.primary","messages":[]}'
    with running_backend(StaticBackend(["x"])) as (host, port):
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Connection: close\r\n\r\n"
            + body
        )
        raw, hit_eof = _raw_roundtrip(host, port, request)

    assert b" 400 " in raw.split(b"\r\n", 1)[0]
    assert hit_eof


def test_transfer_encoding_is_rejected_and_connection_closed():
    with running_backend(StaticBackend(["x"])) as (host, port):
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + b"Transfer-Encoding: identity\r\n"
            + b"Connection: close\r\n\r\n"
        )
        raw, hit_eof = _raw_roundtrip(host, port, request)

    assert b" 411 " in raw.split(b"\r\n", 1)[0]
    assert b"connection: close" in raw.lower()
    assert hit_eof


def test_oversized_content_length_uses_bounded_close():
    import anvil_serving.router.front_door as front_door

    huge_length = front_door.MAX_BODY_BYTES + 1
    with running_backend(StaticBackend(["x"])) as (host, port):
        request = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            + f"Host: {host}:{port}\r\n".encode("ascii")
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {huge_length}\r\n".encode("ascii")
            + b"\r\n"
            + b"x" * 256
        )
        raw, hit_eof = _raw_roundtrip(host, port, request, timeout=5.0)

    assert b" 413 " in raw.split(b"\r\n", 1)[0]
    assert hit_eof


def test_concurrency_cap_returns_503(monkeypatch):
    import anvil_serving.router.front_door as front_door

    exhausted = threading.BoundedSemaphore(1)
    exhausted.acquire()
    monkeypatch.setattr(front_door, "_CONCURRENCY_LIMIT", exhausted)

    with running_backend(StaticBackend(["x"])) as (host, port):
        status, _, raw = _request(
            host,
            port,
            "POST",
            "/v1/chat/completions",
            {
                "model": "llm.primary",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert status == 503
    assert json.loads(raw)["error"]["type"] == "server_busy"


def test_backend_failures_do_not_leak_internal_names_or_exception_text():
    class TierFailure:
        def generate(self, request):
            raise NoAvailableTierError(
                "llm.primary", ("secret-tier",), kind="unavailable"
            )

    with running_backend(TierFailure()) as (host, port):
        status, _, raw = _request(
            host,
            port,
            "POST",
            "/v1/chat/completions",
            {
                "model": "llm.primary",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert status == 503
    assert b"secret-tier" not in raw

    class UnexpectedFailure:
        def generate(self, request):
            raise RuntimeError("supersecret-internal-error")

    with running_backend(UnexpectedFailure()) as (host, port):
        status, headers, raw = _request(
            host,
            port,
            "POST",
            "/v1/chat/completions",
            {
                "model": "llm.primary",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert status == 500
    assert b"supersecret-internal-error" not in raw
    assert headers["Server"].strip() == "anvil"


def test_decision_summary_endpoint_keeps_direct_metadata_only():
    backend = StaticBackend(["ok"])
    backend._decision_log = DecisionLog()
    backend._decision_log.record(DecisionRecord(
        kind="chat",
        requested_tier="primary-local",
        attempts=(AttemptRecord(
            "primary-local", True, "served", 10, 4, "served"
        ),),
        served_tier="primary-local",
        total_prompt_tokens=10,
        total_completion_tokens=4,
        route="llm.primary",
        request_id="req_91ce",
    ))

    with running_backend(backend) as (host, port):
        status, _, raw = _request(
            host, port, "GET", "/v1/decisions?limit=1"
        )

    body = json.loads(raw)
    assert status == 200
    assert body["records"][0]["route"] == "llm.primary"
    assert body["records"][0]["request_id"] == "req_91ce"
    assert "messages" not in json.dumps(body["records"])
