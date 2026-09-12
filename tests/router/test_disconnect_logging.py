"""HTTP-boundary logs distinguish downstream disconnects from upstream faults."""

import io
import json
import re

import pytest

from anvil_serving.router.front_door import make_server
from anvil_serving.router.gateway import MCP_PATH


class Stream:
    def __init__(self, error=None):
        self.error = error
        self.reads = 0
        self.closed = 0

    def __iter__(self):
        return self

    def __next__(self):
        self.reads += 1
        if self.reads == 1:
            return "private-response"
        if self.error is not None:
            raise self.error("private-upstream-detail")
        raise StopIteration

    def close(self):
        self.closed += 1


class Backend:
    def __init__(self, error=None):
        self.stream = Stream(error)

    def generate(self, request):
        return self.stream


class Writer(io.BytesIO):
    def __init__(self, phase, error):
        super().__init__()
        self.phase = phase
        self.error = error

    def write(self, data):
        is_header = data.startswith(b"HTTP/")
        if ((self.phase == "headers" and is_header)
                or (self.phase == "body" and not is_header)
                or (self.phase == "trailer" and data == b"0\r\n\r\n")):
            raise self.error("private-socket-detail")
        return super().write(data)

    def flush(self):
        if self.phase == "flush":
            raise self.error("private-flush-detail")


def request_handler(server, *, stream=True, phase=None, error=BrokenPipeError):
    handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
    body = json.dumps({
        "model": "llm.primary", "stream": stream,
        "messages": [{"role": "user", "content": "private-prompt"}],
    }).encode()
    handler.server = server
    handler.rfile = io.BytesIO(
        b"POST /v1/chat/completions?private-query HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\nContent-Type: application/json\r\n"
        b"Authorization: Bearer private-token\r\n"
        b"X-Request-Id: private-caller-id\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    )
    handler.wfile = Writer(phase, error)
    return handler


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
@pytest.mark.parametrize("stream,phase", [
    (True, "headers"), (True, "body"), (True, "trailer"), (True, "flush"),
    (False, "headers"), (False, "body"), (False, "flush"),
])
def test_disconnect_is_one_private_safe_log_and_releases_stream(capsys, stream, phase, error):
    backend = Backend()
    server = make_server("127.0.0.1", 0, backend, model_routes=("llm.primary",))
    try:
        handler = request_handler(server, stream=stream, phase=phase, error=error)
        handler.handle_one_request()
        log = capsys.readouterr().err
        assert log.count("event=client_disconnected") == 1
        assert f"error={error.__name__}" in log
        assert re.search(r"gateway_request_id=req_[0-9a-f]{32} elapsed_ms=\d+\.\d", log)
        assert "timestamp=" in log
        assert "Traceback" not in log and "private-" not in log
        assert handler.close_connection
        if stream:
            assert backend.stream.closed == 1
        else:
            assert backend.stream.reads == 2  # non-streaming response consumed it
    finally:
        server.server_close()


@pytest.mark.parametrize("error", [BrokenPipeError, ConnectionResetError, TimeoutError, RuntimeError])
def test_upstream_error_is_not_reported_as_client_disconnect(capsys, error):
    backend = Backend(error)
    server = make_server("127.0.0.1", 0, backend, model_routes=("llm.primary",))
    try:
        handler = request_handler(server)
        handler.handle_one_request()
        log = capsys.readouterr().err
        assert f"500 stream error after headers: {error.__name__}" in log
        assert "event=client_disconnected" not in log
        assert "private-" not in log
        assert b'"error"' in handler.wfile.getvalue()
        assert b"private-upstream-detail" not in handler.wfile.getvalue()
        assert handler.close_connection
        assert backend.stream.closed == 1
    finally:
        server.server_close()


@pytest.mark.parametrize("route", ["health", "mcp"])
def test_source_callback_socket_error_is_not_swallowed(capsys, route):
    class SourceFailure(Backend):
        def tier_health(self):
            raise ConnectionResetError("private-source-detail")

        def mcp_request(self, body):
            raise ConnectionResetError("private-source-detail")

    backend = SourceFailure()
    server = make_server("127.0.0.1", 0, backend, gateway=backend, auth_token="private-token")
    try:
        handler = request_handler(server)
        if route == "health":
            raw = (b"GET /v1/health/tiers HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                   b"Authorization: Bearer private-token\r\n\r\n")
        else:
            raw = (f"POST {MCP_PATH} HTTP/1.1\r\nContent-Length: 2\r\n".encode()
                   + b"Authorization: Bearer private-token\r\n\r\n{}")
        handler.rfile = io.BytesIO(raw)
        with pytest.raises(ConnectionResetError, match="private-source-detail"):
            handler.handle_one_request()
        assert "client_disconnected" not in capsys.readouterr().err
    finally:
        server.server_close()


@pytest.mark.parametrize("phase", [None, "headers", "body"])
def test_protocol_stream_classifies_source_and_writer_failures(capsys, monkeypatch, phase):
    backend = Backend()
    server = make_server("127.0.0.1", 0, backend)
    closed = []

    class Frames:
        reads = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.reads += 1
            if self.reads == 1:
                return b"data: {}\n\n"
            raise ConnectionResetError("private-protocol-source")

        def close(self):
            closed.append(True)

    def serve_protocol(handler):
        handler._start_request_correlation()
        handler._write_protocol_sse(Frames())

    monkeypatch.setattr(server.RequestHandlerClass, "do_POST", serve_protocol)
    try:
        handler = request_handler(server, phase=phase)
        handler.handle_one_request()
        log = capsys.readouterr().err
        expected = "event=client_disconnected" if phase else "500 protocol stream error"
        assert expected in log
        assert ("client_disconnected" in log) == bool(phase)
        assert "private-" not in log
        assert handler.close_connection
        assert closed == [True]
    finally:
        server.server_close()


def test_keepalive_disconnect_uses_current_request_id(capsys):
    backend = Backend()
    server = make_server("127.0.0.1", 0, backend, model_routes=("llm.primary",))
    try:
        handler = request_handler(server)
        handler.handle_one_request()
        first_id = handler._correlation_headers()["X-Anvil-Request-Id"]
        assert not handler.close_connection
        handler.rfile = request_handler(server).rfile
        handler.wfile._wrapped.phase = "body"
        handler.handle_one_request()
        second_id = handler._correlation_headers()["X-Anvil-Request-Id"]
        log = capsys.readouterr().err
        assert first_id != second_id
        assert second_id in log and first_id not in log
        assert log.count("event=client_disconnected") == 1
    finally:
        server.server_close()
