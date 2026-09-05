"""Authenticated loopback readiness probes keep credentials on one origin."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from anvil_serving.router.availability import HttpHealthAvailability
from anvil_serving.router.config import RouterConfig, Tier


_TOKEN = "test-readiness-token"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - required BaseHTTPRequestHandler spelling
        self.server.calls.append((self.path, self.headers.get("Authorization")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/redirected")
            self.end_headers()
            return
        if self.path == "/v1/models" and self.headers.get("Authorization") == f"Bearer {_TOKEN}":
            self.send_response(200)
        else:
            self.send_response(401)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 - stdlib hook signature
        return None


@contextmanager
def _protected_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _tier(port: int, *, health_path: str) -> Tier:
    return Tier(
        id="protected-local",
        base_url=f"http://127.0.0.1:{port}/v1",
        model="protected-model",
        dialect="openai",
        context_limit=4096,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_READINESS_TOKEN",
        health_path=health_path,
    )


def _availability(tier: Tier, env: dict[str, str]) -> HttpHealthAvailability:
    config = RouterConfig(
        tiers=(tier,), model_routes={"llm.primary": tier.id},
        availability_probe_interval=0.0, availability_probe_timeout=1.0,
        availability_probe_max_bytes=4096,
    )
    return HttpHealthAvailability(config, env=env)


def test_authenticated_v1_models_health_probe_succeeds_on_loopback():
    with _protected_server() as server:
        tier = _tier(server.server_address[1], health_path="/v1/models")
        result = _availability(tier, {"ANVIL_TEST_READINESS_TOKEN": _TOKEN}).check(tier)

    assert result.available is True
    assert result.reason == "health_passed"
    assert server.calls == [("/v1/models", f"Bearer {_TOKEN}")]


def test_health_redirect_is_denied_without_following_credential():
    with _protected_server() as server:
        tier = _tier(server.server_address[1], health_path="/redirect")
        result = _availability(tier, {"ANVIL_TEST_READINESS_TOKEN": _TOKEN}).check(tier)

    assert result.available is False
    assert result.reason == "health_http_302"
    assert server.calls == [("/redirect", f"Bearer {_TOKEN}")]
    assert _TOKEN not in repr(result)


def test_missing_health_token_fails_closed_with_bounded_status():
    with _protected_server() as server:
        tier = _tier(server.server_address[1], health_path="/v1/models")
        result = _availability(tier, {}).check(tier)

    assert result.available is False
    assert result.reason == "health_http_401"
    assert server.calls == [("/v1/models", None)]
