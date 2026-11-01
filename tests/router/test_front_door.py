"""HTTP contract for the authenticated direct capability gateway."""
from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path


from tests.router.helpers import StaticBackend
from anvil_serving.router.config import load
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import NoAvailableTierError
from anvil_serving.router.serve import RoutingBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"
_TOKEN = "test-gateway-token"


@contextmanager
def running_server(*, auth=False, max_output_tokens=None):
    config = load(_CONFIG)
    if max_output_tokens is not None:
        config = replace(
            config,
            tiers=tuple(
                replace(tier, max_output_tokens=max_output_tokens)
                if tier.id == "primary-local"
                else tier
                for tier in config.tiers
            ),
        )
    routing = RoutingBackend(config, {
        "primary-local": StaticBackend(["heavy response"]),
        "omni-local": StaticBackend(["omni response"]),
    })
    server = make_server(
        "127.0.0.1", 0, routing, model_routes=config.model_routes,
        auth_token=_TOKEN if auth else None,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def test_unknown_alias_returns_clean_404_without_tier_identity():
    with running_server() as (host, port):
        status, _, raw = _request(host, port, "POST", "/v1/chat/completions", {
            "model": "missing", "messages": [{"role": "user", "content": "hi"}],
        })

    assert status == 404
    assert json.loads(raw)["error"]["type"] == "model_not_found"
    assert "primary-local" not in raw.decode()


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
