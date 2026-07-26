"""Model discovery exposes only configured direct capability aliases."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.front_door import make_server


@contextmanager
def _server(routes=("llm.primary", "llm.voice")):
    server = make_server("127.0.0.1", 0, StaticBackend(["ok"]), model_routes=routes)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _models(host, port):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        connection.request("GET", "/v1/models")
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_models_endpoint_advertises_exact_configured_aliases():
    with _server() as (host, port):
        status, payload = _models(host, port)

    assert status == 200
    assert payload["object"] == "list"
    assert [model["id"] for model in payload["data"]] == ["llm.primary", "llm.voice"]
    assert all(model["object"] == "model" for model in payload["data"])


def test_models_endpoint_is_deterministic_and_does_not_invent_presets():
    with _server(("llm.voice",)) as (host, port):
        first_status, first = _models(host, port)
        second_status, second = _models(host, port)

    assert first_status == second_status == 200
    assert first == second
    assert [model["id"] for model in first["data"]] == ["llm.voice"]
    assert "chat" not in json.dumps(first)
