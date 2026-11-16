"""HTTP-boundary logs distinguish downstream disconnects from upstream faults."""

import io
import json

import pytest

from anvil_serving.router.front_door import make_server


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
