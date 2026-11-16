"""Bounded local HTTP ingress with mock model ownership; no live services."""

import io
import http.client
import queue
import threading
from email.message import Message
from types import SimpleNamespace

import pytest

from anvil_serving import multiplexer


def test_dribbling_body_cannot_extend_the_absolute_deadline(monkeypatch):
    handler = object.__new__(multiplexer.make_handler(None))
    handler.path = "/v1/completions"
    handler.headers = Message()
    handler.headers["Content-Length"] = "100"
    timeouts = []
    handler.connection = SimpleNamespace(gettimeout=lambda: 30, settimeout=timeouts.append)
    reads = []
    handler.rfile = SimpleNamespace(read1=lambda count: reads.append(count) or b"x")
    moments = iter([0, 10, 20, 31])
    monkeypatch.setattr(multiplexer.time, "monotonic", lambda: next(moments))
    errors = []
    handler._err = lambda *args: errors.append(args)
    handler.do_POST()
    assert errors[0][0] == 408 and handler.close_connection
    assert len(reads) == 2 and timeouts[:2] == [20, 10] and timeouts[-1] == 30


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(multiplexer, "_REQUEST_BODY_TIMEOUT_SECONDS", 0.2)
    backend = multiplexer.MockBackend()
    mux = multiplexer.Multiplexer([
        {"name": "selected", "model_path": "/models/fixture", "est_weight_gb": 1, "gpu": 0, "port": 30000},
    ], backend, ram_probe=lambda: 100)
    accepted = queue.Queue()

    class Handler(multiplexer.make_handler(mux)):
        def setup(self):
            super().setup()
            accepted.put(self)

    httpd = multiplexer._MultiplexerHTTPServer(("127.0.0.1", 0), Handler, maximum_connections=2)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield httpd, accepted, backend
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("streaming", [False, True])
def test_valid_body_and_response_are_forwarded_verbatim(server, monkeypatch, streaming):
    httpd, accepted, backend = server
    monkeypatch.setattr(multiplexer, "_REQUEST_HEADER_TIMEOUT_SECONDS", 0.2)
    payload = b'{ "model": "selected", "messages": [] }'
    output = b'data: {"choices":[]}\n\ndata: [DONE]\n\n' if streaming else b'{"choices":[]}'
    response = io.BytesIO(output)
    response.status = 200
    response.headers = {"Content-Type": "text/event-stream" if streaming else "application/json"}
    if not streaming:
        response.headers["Content-Length"] = str(len(output))
    calls = []

    def upstream(url, body):
        calls.append((url, body))
        handler = accepted.get(timeout=2)
        assert handler.connection.gettimeout() == multiplexer._CLIENT_SOCKET_TIMEOUT_SECONDS
        # The header deadline must not cut off inference or leak into the next
        # keep-alive request, even when inference outlasts that budget.
        threading.Event().wait(0.3)
        return response

    monkeypatch.setattr(multiplexer, "_open_upstream", upstream)
    connection = http.client.HTTPConnection(*httpd.server_address, timeout=2)
    try:
        connection.request("POST", "/v1/chat/completions", body=payload,
                           headers={"Content-Length": "\t" + "0" * 5000 + str(len(payload)) + " \t"})
        result = connection.getresponse()
        assert result.status == 200 and result.read() == output
        assert result.getheader("Content-Type") == response.headers["Content-Type"]
        if not streaming:
            # Keep-alive still accepts the next healthy request.
            connection.request("GET", "/v1/models")
            result = connection.getresponse()
            assert result.status == 200
            result.read()
    finally:
        connection.close()
    assert calls == [("http://mock/selected/chat/completions", payload)]
    assert backend.started == ["selected"]
