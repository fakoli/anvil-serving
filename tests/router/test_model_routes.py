"""Direct-only chat capability gateway coverage."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.serve import build_server


_CONFIG = """\
[router]

[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31002/v1"
dialect = "openai"
context_limit = 16
max_output_tokens = 8
privacy = "local"
tool_support = true
auth_env = "ANVIL_PRIMARY_KEY"
model = "primary-model"

[router.model_routes]
llm.primary = "primary"
"""


class CountingBackend:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield from self.tokens


class Unavailable:
    def check(self, tier):
        return AvailabilityResult(False, "unavailable", "test_unavailable")


@contextmanager
def _running(server):
    host, port = server.server_address[:2]
    # server_close then joins the request handler, so assertions never rely on
    # the accept loop's polling delay to observe terminal decision logging.
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(host, port, body, *, stream=False):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        body = {**body, "stream": stream}
        connection.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.fixture
def config_path(tmp_path: Path) -> str:
    path = tmp_path / "direct.toml"
    path.write_text(_CONFIG, encoding="utf-8")
    return str(path)


def _server(config_path, backend, **kwargs):
    return build_server(config_path, host="127.0.0.1", port=0, backends={"primary": backend}, **kwargs)


def test_direct_route_has_no_fallback_for_unavailable_context_or_quiesced(config_path):
    backend = CountingBackend(["must not run"])
    server = _server(config_path, backend, availability=Unavailable())
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 503
    assert backend.calls == 0

    backend = CountingBackend(["must not run"])
    server = _server(config_path, backend)
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "word " * 20}],
        })
    assert status == 413
    assert backend.calls == 0

    server = _server(config_path, backend)
    server.anvil_routing.quiesce_tier("primary", "test")
    with _running(server) as (host, port):
        status, _ = _post(host, port, {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hello"}],
        })
    assert status == 503
    assert backend.calls == 0
