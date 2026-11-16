"""Unauthenticated telemetry/dashboard listener rejects foreign Host (DNS rebinding)."""

from __future__ import annotations

import http.client

import pytest

from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    create_server,
    run_server_in_thread,
)

TOKEN = "api-host-guard-fixture-token"


def _registry():
    return TelemetryRegistry([ProbeRegistration("host-resources", lambda: [], "generic-host", "fixture")])


def _status(port, method, host, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, "/health", skip_host=True)
        conn.putheader("Host", host)
        for name, value in (headers or {}).items():
            conn.putheader(name, value)
        conn.putheader("Content-Length", "0")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


@pytest.fixture
def serve():
    started = []

    def factory(**kwargs):
        server = create_server(
            _registry(),
            static_routes={"/": ("text/html", b"<p>ok</p>")},
            public_static_routes=["/"],
            **kwargs,
        )
        started.append((server, run_server_in_thread(server)))
        return server.server_address[1]

    yield factory
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_foreign_host_without_token_is_forbidden(serve, method):
    port = serve()
    assert _status(port, method, "attacker.example:%d" % port) == 403


def test_loopback_host_without_token_is_served(serve):
    port = serve()
    assert _status(port, "GET", "127.0.0.1:%d" % port) == 200
    assert _status(port, "GET", "[::1]:%d" % port) == 200


def test_foreign_host_cannot_read_public_static_route(serve):
    port = serve()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", "attacker.example")
        conn.endheaders()
        assert conn.getresponse().status == 403
    finally:
        conn.close()


def test_foreign_host_with_token_is_unaffected(serve):
    port = serve(auth_env="ANVIL_TELEMETRY_TOKEN", environment={"ANVIL_TELEMETRY_TOKEN": TOKEN})
    headers = {"Authorization": "Bearer " + TOKEN}
    assert _status(port, "GET", "dashboard.example.ts.net", headers) == 200
