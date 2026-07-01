"""Front-door token auth (router-service:T001 / ADR-0004).

Hermetic: starts the REAL server on an ephemeral 127.0.0.1 port with a
deterministic StaticBackend and a directly-injected ``auth_token`` (the value
`serve.build_server` would have resolved from `os.environ[auth_env]` at
startup -- this file doesn't need env vars or a TOML config on disk to prove
the front-door contract). Config-level validation of ``[server].auth_env``
lives in ``test_config.py`` (see the ``-k "server or auth_env"`` verification
command).

Proves:
  - correct Bearer token -> routed 200
  - correct x-api-key token -> routed 200
  - wrong / missing token -> 401 JSON (constant-time compare; token never
    appears in any response or log)
  - auth_token=None (unset [server].auth_env) -> every request accepted, no
    auth at all -- identical to pre-T001 behaviour
  - GET /healthz -> 200 with AND without a token, even when auth is ON
  - GET /health (the alias) is NOT exempt -- it requires auth like every
    other route once auth is configured
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import Dict, Optional, Tuple

from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.front_door import make_server

TOKEN = "s3cr3t-router-token"


@contextmanager
def running_server(backend, auth_token: Optional[str]):
    """Start the front door on an ephemeral port with a fixed ``auth_token``."""
    httpd = make_server("127.0.0.1", 0, backend, auth_token=auth_token)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(host: str, port: int, path: str, body: dict,
          headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        payload = json.dumps(body)
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        conn.request("POST", path, payload, h)
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        data = resp.read()
        return resp.status, resp_headers, data
    finally:
        conn.close()


def _get(host: str, port: int, path: str,
         headers: Optional[Dict[str, str]] = None) -> Tuple[int, Dict[str, str], bytes]:
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        data = resp.read()
        return resp.status, resp_headers, data
    finally:
        conn.close()


_CHAT_BODY = {
    "model": "chat",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": False,
}


# --------------------------------------------------------------------------- #
# auth ON: correct token -> 200
# --------------------------------------------------------------------------- #
def test_bearer_token_correct_routes_200():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                               headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    obj = json.loads(raw)
    assert obj["choices"][0]["message"]["content"] == "ok"


def test_x_api_key_correct_routes_200():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                               headers={"x-api-key": TOKEN})
    assert status == 200
    obj = json.loads(raw)
    assert obj["choices"][0]["message"]["content"] == "ok"


def test_anthropic_route_bearer_token_correct_routes_200():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _post(host, port, "/v1/messages", {
            "model": "claude",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        }, headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200


# --------------------------------------------------------------------------- #
# auth ON: wrong / missing token -> 401 JSON
# --------------------------------------------------------------------------- #
def test_wrong_bearer_token_401():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, headers, raw = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                                     headers={"Authorization": "Bearer wrong-token"})
    assert status == 401
    assert headers.get("content-type") == "application/json"
    obj = json.loads(raw)
    assert obj["error"]["type"] == "authentication_error"
    # The correct token never leaks into the error body.
    assert TOKEN not in raw.decode("utf-8")


def test_wrong_x_api_key_401():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                               headers={"x-api-key": "wrong-token"})
    assert status == 401
    assert json.loads(raw)["error"]["type"] == "authentication_error"


def test_missing_token_401():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _post(host, port, "/v1/chat/completions", _CHAT_BODY)
    assert status == 401
    assert json.loads(raw)["error"]["type"] == "authentication_error"


def test_malformed_authorization_scheme_401():
    """A non-Bearer Authorization scheme is treated as no token supplied."""
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, _ = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                             headers={"Authorization": f"Basic {TOKEN}"})
    assert status == 401


def test_unknown_route_still_401_when_unauthenticated():
    """Unauthenticated callers get a uniform 401, not a 404 (no route-enumeration oracle)."""
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, _ = _post(host, port, "/v1/nope", _CHAT_BODY)
    assert status == 401


# --------------------------------------------------------------------------- #
# auth_env unset -> auth OFF, identical to pre-T001 behaviour
# --------------------------------------------------------------------------- #
def test_auth_off_when_no_token_configured_accepts_all():
    with running_server(StaticBackend(["ok"]), auth_token=None) as (host, port):
        status_no_header, _, _ = _post(host, port, "/v1/chat/completions", _CHAT_BODY)
        status_wrong_header, _, _ = _post(host, port, "/v1/chat/completions", _CHAT_BODY,
                                          headers={"Authorization": "Bearer whatever"})
    assert status_no_header == 200
    assert status_wrong_header == 200


# --------------------------------------------------------------------------- #
# GET /healthz: always unauthenticated
# --------------------------------------------------------------------------- #
def test_healthz_open_with_auth_on_and_no_token():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _get(host, port, "/healthz")
    assert status == 200
    assert json.loads(raw)["status"] == "ok"


def test_healthz_open_with_auth_on_and_correct_token():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status, _, raw = _get(host, port, "/healthz",
                              headers={"Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    assert json.loads(raw)["status"] == "ok"


def test_healthz_open_with_auth_off():
    with running_server(StaticBackend(["ok"]), auth_token=None) as (host, port):
        status, _, raw = _get(host, port, "/healthz")
    assert status == 200
    assert json.loads(raw)["status"] == "ok"


def test_health_alias_is_not_auth_exempt():
    """Only the literal /healthz path is exempt; /health requires auth like
    every other route once [server].auth_env is configured."""
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status_unauthed, _, _ = _get(host, port, "/health")
        status_authed, _, raw = _get(host, port, "/health",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
    assert status_unauthed == 401
    assert status_authed == 200
    assert json.loads(raw)["status"] == "ok"


def test_v1_models_requires_auth():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status_unauthed, _, _ = _get(host, port, "/v1/models")
        status_authed, _, _ = _get(host, port, "/v1/models",
                                   headers={"x-api-key": TOKEN})
    assert status_unauthed == 401
    assert status_authed == 200
