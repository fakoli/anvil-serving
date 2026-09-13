"""Loopback coverage for managed request lifetime and client budgets."""
from __future__ import annotations

import json
import os
import socket
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving.control_plane.authorization import INFERENCE_USE, WORKLOADS_READ, load_authorization_policy
from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import RouterConfig, ServerConfig
from anvil_serving.router.front_door import OperatorRoute, make_server
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import make_tier


class _Upstream:
    """A real loopback OpenAI-wire server that can pause before first bytes."""

    def __init__(self, engine: str = "vllm", *, first_then_silent: bool = False) -> None:
        self.engine = engine
        self.opened = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()
        self.requests = []
        self.first_then_silent = first_then_silent
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                owner.requests.append(json.loads(self.rfile.read(length)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.flush()
                owner.opened.set()
                try:
                    if owner.first_then_silent:
                        self.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"first"}}]}\n\n')
                        self.wfile.flush()
                        owner.release.wait(2)
                        return
                    owner.release.wait(2)
                    if not owner.release.is_set():
                        return
                    usage = {
                        "vllm": {"prompt_tokens": 3, "completion_tokens": 1,
                                  "prompt_tokens_details": {"cached_tokens": 1}},
                        "sglang": {"prompt_tokens": 3, "completion_tokens": 1,
                                   "reasoning_tokens": 1},
                        "llamacpp": {"prompt_tokens": 3, "completion_tokens": 1},
                    }[owner.engine]
                    for item in (
                        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                        {"choices": [], "usage": usage},
                    ):
                        self.wfile.write(b"data: " + json.dumps(item).encode() + b"\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    owner.closed.set()

            def log_message(self, *_args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    @property
    def base_url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"


class _DripHeadersUpstream:
    """Keep an HTTP status header incomplete while regularly sending bytes."""

    def __init__(self) -> None:
        self.opened = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.wfile.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nX-Drip: "
                )
                self.wfile.flush()
                owner.opened.set()
                try:
                    for _ in range(200):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.01)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def log_message(self, *_args):
                pass

        owner = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    @property
    def base_url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"


class _BufferedKeepaliveUpstream:
    """Send one complete JSON body, then retain the HTTP/1.1 connection."""

    def __init__(self) -> None:
        self.opened = threading.Event()
        self.release = threading.Event()
        self.body = json.dumps({
            "choices": [{"message": {"content": "prompt"}, "finish_reason": "stop"}],
        }).encode()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                self.wfile.write(owner.body)
                self.wfile.flush()
                owner.opened.set()
                owner.release.wait(3)

            def log_message(self, *_args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)

    @property
    def base_url(self):
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1"


@contextmanager
def _gateway(upstream, config: ServerConfig, *, auth_token=None, policy=None, routes=(), max_concurrency=None):
    tier = replace(make_tier("openai"), id="loopback", base_url=upstream.base_url,
                   max_concurrency=max_concurrency)
    routing = RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"chat": tier.id}),
        {tier.id: RelayBackend(tier, env={})},
    )
    server = make_server(
        "127.0.0.1", 0, routing, model_routes=("chat",), auth_token=auth_token,
        authorization_policy=policy, operator_routes=routes, server_config=config,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2], routing
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _post(sock, *, stream=True, token=None, session_id=None, request_id=None, payload=None):
    payload = payload if payload is not None else json.dumps({
        "model": "chat", "stream": stream,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()
    headers = b"" if token is None else f"Authorization: Bearer {token}\r\n".encode()
    session = b"" if session_id is None else f"X-Anvil-Session-Id: {session_id}\r\n".encode()
    request = b"" if request_id is None else f"X-Request-Id: {request_id}\r\n".encode()
    sock.sendall(
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nConnection: close\r\n" + headers + session + request
        + f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload
    )


def _post_headers_only(sock, *, token, content_length):
    sock.sendall(
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nConnection: close\r\n"
        + f"Authorization: Bearer {token}\r\nContent-Length: {content_length}\r\n\r\n".encode()
    )


def _read_until(sock, needle, timeout=1.0, *, body_only=False):
    """Read until ``needle``, optionally requiring it after HTTP headers."""
    end = time.monotonic() + timeout
    chunks = []
    sock.settimeout(0.05)
    while time.monotonic() < end:
        try:
            part = sock.recv(65536)
        except TimeoutError:
            continue
        if not part:
            break
        chunks.append(part)
        wire = b"".join(chunks)
        header_end = wire.find(b"\r\n\r\n")
        if needle in (wire[header_end + 4:] if body_only and header_end >= 0 else wire) and (
            not body_only or header_end >= 0
        ):
            break
    return b"".join(chunks)


@pytest.mark.parametrize("engine", ("vllm", "sglang", "llamacpp"))
def test_managed_runtime_heartbeats_then_relays_all_openai_engines(engine):
    settings = ServerConfig(startup_timeout_s=1, idle_timeout_s=1, total_timeout_s=2,
                            heartbeat_interval_s=0.03)
    with _Upstream(engine) as upstream, _gateway(upstream, settings) as ((host, port), _routing):
        with socket.create_connection((host, port), timeout=1) as client:
            _post(client)
            assert upstream.opened.wait(1)
            assert b": keepalive\n\n" in _read_until(client, b": keepalive\n\n")
            upstream.release.set()
            assert b'"content":"ok"' in _read_until(client, b"[DONE]")


def test_silent_upstream_hits_startup_deadline_despite_heartbeats():
    settings = ServerConfig(startup_timeout_s=0.08, idle_timeout_s=1, total_timeout_s=1,
                            heartbeat_interval_s=0.02)
    with _Upstream() as upstream, _gateway(upstream, settings) as ((host, port), routing):
        with socket.create_connection((host, port), timeout=1) as client:
            _post(client)
            assert upstream.opened.wait(1)
            wire = _read_until(client, b"0\r\n\r\n", timeout=1, body_only=True)
    assert b": keepalive\n\n" in wire
    assert routing._decision_log.last is not None
    assert routing._decision_log.last.attempts[0].outcome == "error"


def test_dripped_response_headers_cannot_outlive_startup_deadline_or_admission():
    settings = ServerConfig(startup_timeout_s=0.08, idle_timeout_s=1, total_timeout_s=1,
                            heartbeat_interval_s=0.02)
    with _DripHeadersUpstream() as upstream, _gateway(upstream, settings) as ((host, port), routing):
        with socket.create_connection((host, port), timeout=1) as client:
            # The response mirrors this ID in a header.  Its final ``0`` plus
            # the header terminator must not be mistaken for a chunk terminator.
            _post(client, request_id="dripped-response-id0")
            assert upstream.opened.wait(1)
            wire = _read_until(client, b"0\r\n\r\n", timeout=1, body_only=True)
    # Dispatch can commit SSE headers before the upstream finishes parsing
    # headers.  A startup deadline is therefore a typed 504 before commitment
    # or the native terminal SSE error after commitment; it must never become
    # the generic internal 500 caused by a watchdog/socket race.
    assert b" 500 " not in wire
    assert (
        (b" 504 " in wire and b"startup_timeout" in wire)
        or b"upstream_error" in wire
    )
    assert routing._admission.snapshot("loopback").active_requests == 0


def test_buffered_dripped_headers_cannot_outlive_startup_deadline_or_admission():
    settings = ServerConfig(startup_timeout_s=0.08, idle_timeout_s=1, total_timeout_s=1,
                            heartbeat_interval_s=0.02)
    with _DripHeadersUpstream() as upstream, _gateway(upstream, settings) as ((host, port), routing):
        with socket.create_connection((host, port), timeout=1) as client:
            _post(client, stream=False)
            assert upstream.opened.wait(1)
            wire = _read_until(client, b"\r\n\r\n", timeout=1)
    assert b" 504 " in wire
    assert routing._admission.snapshot("loopback").active_requests == 0


def test_buffered_content_length_returns_before_upstream_keepalive_closes():
    settings = ServerConfig(startup_timeout_s=1, idle_timeout_s=1, total_timeout_s=5,
                            heartbeat_interval_s=0.02)
    with _BufferedKeepaliveUpstream() as upstream, _gateway(upstream, settings, max_concurrency=1) as ((host, port), routing):
        with socket.create_connection((host, port), timeout=1) as client:
            started = time.monotonic()
            _post(client, stream=False, session_id="session-buffered")
            assert upstream.opened.wait(1)
            wire = _read_until(client, b'"content": "prompt"', timeout=0.5)
        assert time.monotonic() - started < 1
        assert not upstream.release.is_set()
        assert b" 200 " in wire
        assert b'"content": "prompt"' in wire
        deadline = time.monotonic() + 0.5
        while routing._admission.snapshot("loopback").active_requests and time.monotonic() < deadline:
            time.sleep(0.01)
        assert routing._admission.snapshot("loopback").active_requests == 0
        record = routing._decision_log.last
        assert record is not None
        assert record.session_id == "session-buffered"
        assert record.attempts[-1].succeeded is True


@pytest.mark.parametrize(("idle_timeout_s", "total_timeout_s"), ((0.07, 1), (1, 0.07)))
def test_first_byte_then_silence_obeys_idle_and_total_deadlines(idle_timeout_s, total_timeout_s):
    settings = ServerConfig(startup_timeout_s=1, idle_timeout_s=idle_timeout_s,
                            total_timeout_s=total_timeout_s, heartbeat_interval_s=0.02)
    with _Upstream(first_then_silent=True) as upstream, _gateway(upstream, settings) as ((host, port), routing):
        with socket.create_connection((host, port), timeout=1) as client:
            _post(client)
            assert upstream.opened.wait(1)
            wire = _read_until(client, b"0\r\n\r\n", timeout=1, body_only=True)
    assert b'"content":"first"' in wire
    assert routing._decision_log.last.attempts[0].outcome == "error"


def test_admission_timeout_returns_503_before_stream_headers():
    settings = ServerConfig(admission_timeout_s=0.05, startup_timeout_s=1, idle_timeout_s=1,
                            total_timeout_s=2, heartbeat_interval_s=0.02)
    with _Upstream() as upstream, _gateway(upstream, settings, max_concurrency=1) as ((host, port), routing):
        first = socket.create_connection((host, port), timeout=1)
        try:
            _post(first)
            assert upstream.opened.wait(1)
            with socket.create_connection((host, port), timeout=1) as second:
                _post(second)
                wire = _read_until(second, b"\r\n\r\n", timeout=1)
            assert b" 503 " in wire
            assert b"Retry-After: 1" in wire
            assert b"text/event-stream" not in wire
            assert routing._decision_log.last.admission_wait_ms is not None
        finally:
            first.close()


@pytest.mark.parametrize("auto", [False, True])
def test_cancellation_at_gate_acquisition_does_not_leak_permit(auto):
    from anvil_serving.router.request_control import RequestControl, RequestCancelledError
    from anvil_serving.router.internal import InternalRequest
    from anvil_serving.router.serve import _AutoConcurrencyGate, _ConcurrencyLimitedBackend

    class Control(RequestControl):
        def end_admission_wait(self):
            super().end_admission_wait()
            self.cancel()

    class Backend:
        def generate(self, request):
            pytest.fail("cancelled request reached the backend")

    gate = _AutoConcurrencyGate(Backend(), "test") if auto else _ConcurrencyLimitedBackend(Backend(), 1)
    request = InternalRequest(model="chat", messages=[], raw={"_anvil_control": Control()})
    with pytest.raises(RequestCancelledError):
        gate.generate(request)
    if auto:
        assert gate._in_flight == 0
    else:
        assert gate._sem.acquire(blocking=False)
        gate._sem.release()


def test_worker_cleanup_failure_does_not_prevent_permit_release():
    from anvil_serving.router.front_door_runtime import DeliveryWorker
    from anvil_serving.router.request_control import RequestControl

    proceed, released = threading.Event(), threading.Event()
    worker = DeliveryWorker(lambda send: proceed.wait(1), RequestControl())

    def broken_observer():
        raise RuntimeError("private observer details")

    worker.when_finished(broken_observer)
    worker.when_finished(released.set)
    proceed.set()
    assert released.wait(1)
    worker.close()


@pytest.mark.parametrize("stream", (False, True))
def test_client_disconnect_interrupts_silent_upstream_and_releases_admission_once(stream):
    # Cleanup must beat both the backend silence and the configured deadline.
    settings = ServerConfig(startup_timeout_s=10, idle_timeout_s=10, total_timeout_s=20,
                            heartbeat_interval_s=0.02)
    with _Upstream() as upstream, _gateway(upstream, settings, max_concurrency=1) as ((host, port), routing):
        client = socket.create_connection((host, port), timeout=1)
        _post(client, stream=stream)
        assert upstream.opened.wait(1)
        linger_format = "HH" if os.name == "nt" else "ii"
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(linger_format, 1, 0))
        client.close()
        end = time.monotonic() + 1
        while routing._admission.snapshot("loopback").active_requests and time.monotonic() < end:
            time.sleep(0.01)
        assert routing._admission.snapshot("loopback").active_requests == 0
        assert routing._decision_log.last.attempts[0].reason == "client_disconnected"
        with socket.create_connection((host, port), timeout=1) as next_client:
            _post(next_client)
            assert b" 200 " in _read_until(next_client, b"\r\n\r\n")


def test_write_half_closed_client_still_receives_managed_stream():
    settings = ServerConfig(startup_timeout_s=1, idle_timeout_s=1, total_timeout_s=2,
                            heartbeat_interval_s=0.02)
    with _Upstream() as upstream, _gateway(upstream, settings) as ((host, port), _routing):
        with socket.create_connection((host, port), timeout=1) as client:
            _post(client)
            client.shutdown(socket.SHUT_WR)
            assert upstream.opened.wait(1)
            assert b": keepalive\n\n" in _read_until(client, b": keepalive\n\n", timeout=1)
            upstream.release.set()
            wire = _read_until(client, b'"content":"ok"', timeout=1)
    assert b'"content":"ok"' in wire


def test_inference_client_has_its_own_cap_and_cannot_read_operator_route(tmp_path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"schema_version": 1, "clients": [
        {"id": "limited", "scopes": [INFERENCE_USE], "credential_env": "LIMITED"},
        {"id": "other", "scopes": [INFERENCE_USE], "credential_env": "OTHER"},
    ]}), encoding="utf-8")
    policy = load_authorization_policy(str(policy_path), env={
        "LIMITED": "limited-token-012345", "OTHER": "other-token-012345",
    })
    settings = ServerConfig(client_limits={"limited": 1}, startup_timeout_s=1, idle_timeout_s=1,
                            total_timeout_s=2, heartbeat_interval_s=0.02)
    route = OperatorRoute("GET", "/v1/operator/protected", WORKLOADS_READ, lambda _query: b"{}")
    with _Upstream() as upstream, _gateway(upstream, settings, auth_token="legacy", policy=policy, routes=(route,)) as ((host, port), _routing):
        first = socket.create_connection((host, port), timeout=1)
        try:
            _post(first, token="limited-token-012345")
            assert upstream.opened.wait(1)
            with socket.create_connection((host, port), timeout=1) as limited:
                _post(limited, token="limited-token-012345")
                assert b" 429 " in _read_until(limited, b"\r\n\r\n")
            with socket.create_connection((host, port), timeout=1) as other:
                _post(other, token="other-token-012345")
                assert b" 200 " in _read_until(other, b"\r\n\r\n")
            with socket.create_connection((host, port), timeout=1) as management:
                management.sendall(b"GET /v1/operator/protected HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer limited-token-012345\r\nConnection: close\r\n\r\n")
                assert b" 403 " in _read_until(management, b"\r\n\r\n")
        finally:
            first.close()


def test_client_cap_covers_slow_body_and_releases_after_close_or_bad_json(tmp_path):
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"schema_version": 1, "clients": [
        {"id": "limited", "scopes": [INFERENCE_USE], "credential_env": "LIMITED"},
        {"id": "other", "scopes": [INFERENCE_USE], "credential_env": "OTHER"},
    ]}), encoding="utf-8")
    policy = load_authorization_policy(str(policy_path), env={
        "LIMITED": "limited-token-012345", "OTHER": "other-token-012345",
    })
    settings = ServerConfig(client_limits={"limited": 1}, startup_timeout_s=1,
                            idle_timeout_s=1, total_timeout_s=2, heartbeat_interval_s=0.02)
    body = json.dumps({"model": "chat", "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    with _Upstream() as upstream, _gateway(
        upstream, settings, auth_token="legacy", policy=policy,
    ) as ((host, port), _routing):
        first = socket.create_connection((host, port), timeout=1)
        try:
            _post_headers_only(first, token="limited-token-012345", content_length=len(body))
            # The handler must parse/authenticate the headers and reserve this
            # client's budget before the next request tests the held slot.
            time.sleep(0.05)
            with socket.create_connection((host, port), timeout=1) as refused:
                _post(refused, token="limited-token-012345")
                assert b" 429 " in _read_until(refused, b"\r\n\r\n")
            with socket.create_connection((host, port), timeout=1) as other:
                _post(other, token="other-token-012345")
                assert upstream.opened.wait(1)
                assert b" 200 " in _read_until(other, b"\r\n\r\n")
            first.shutdown(socket.SHUT_RDWR)
            first.close()

            end = time.monotonic() + 1
            while True:
                with socket.create_connection((host, port), timeout=1) as retried:
                    _post(retried, token="limited-token-012345", payload=b"{")
                    wire = _read_until(retried, b"\r\n\r\n")
                if b" 400 " in wire:
                    break
                assert b" 429 " in wire
                assert time.monotonic() < end
                time.sleep(0.01)
            assert len(upstream.requests) == 1  # aborted upload never invokes inference
            with socket.create_connection((host, port), timeout=1) as admitted:
                _post(admitted, token="limited-token-012345")
                assert b" 200 " in _read_until(admitted, b"\r\n\r\n")
        finally:
            first.close()
