"""The unauthenticated multiplexer rejects foreign Host headers (DNS rebinding)."""

import http.client
import socket
import threading

import pytest

from anvil_serving import multiplexer


@pytest.fixture
def port():
    mux = multiplexer.Multiplexer([
        {"name": "selected", "model_path": "/models/fixture", "est_weight_gb": 1, "gpu": 0, "port": 30000},
    ], multiplexer.MockBackend(), ram_probe=lambda: 100)
    httpd = multiplexer._MultiplexerHTTPServer(("127.0.0.1", 0), multiplexer.make_handler(mux))
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _status(port, method, path, host):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host)
        body = b'{"model": "selected", "messages": []}' if method == "POST" else b""
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


@pytest.mark.parametrize("method,path", [("GET", "/v1/models"), ("POST", "/v1/chat/completions")])
def test_foreign_host_is_forbidden(port, method, path):
    assert _status(port, method, path, "attacker.example:%d" % port) == 403


def test_missing_host_is_forbidden(port):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(b"GET /v1/models HTTP/1.1\r\n\r\n")
        assert sock.recv(4096).startswith(b"HTTP/1.1 403")


def test_loopback_host_is_served(port):
    assert _status(port, "GET", "/v1/models", "127.0.0.1:%d" % port) == 200
