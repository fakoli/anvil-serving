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
import socket
import threading
from contextlib import contextmanager
from typing import Dict, Optional, Sequence, Tuple

import pytest

from anvil_serving.control_plane.authorization import (
    NODE_ADMIN_BOOTSTRAP,
    WORKLOADS_READ,
    load_authorization_policy,
)
import anvil_serving.router.front_door as front_door
from tests.router.helpers import StaticBackend
from anvil_serving.router.front_door import (
    TRANSITION_ENDPOINT,
    OperatorRoute,
    make_server,
)

TOKEN = "s3cr3t-router-token"


@contextmanager
def running_server(
    backend,
    auth_token: Optional[str],
    *,
    authorization_policy=None,
    operator_routes: Optional[Sequence[OperatorRoute]] = None,
):
    """Start the front door on an ephemeral port with a fixed ``auth_token``."""
    httpd = make_server(
        "127.0.0.1", 0, backend, auth_token=auth_token,
        authorization_policy=authorization_policy, operator_routes=operator_routes,
    )
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


class _TransitionBackend(StaticBackend):
    def __init__(self):
        super().__init__(["ok"])
        self.state = "admitting"

    def transition_status(self, tier_id=None):
        return {"tiers": [{
            "tier_id": tier_id or "primary-local",
            "state": self.state,
            "reason": "promotion" if self.state == "quiesced" else "admitting",
            "active_requests": 0,
            "ready": True,
            "expected_model": "heavy",
            "observed_model": "heavy",
        }]}

    def quiesce_tier(self, tier_id, reason="promotion"):
        self.state = "quiesced"
        return self.transition_status(tier_id)["tiers"][0]

    def drain_tier(self, tier_id, timeout):
        return {"drained": True, "timed_out": False}

    def readmit_tier(self, tier_id):
        self.state = "admitting"
        return {"readmitted": True, "reason": "readiness_passed"}


def _scoped_policy(tmp_path, clients):
    policy_path = tmp_path / "operator-policy.json"
    env = {client["credential_env"]: client["token"] for client in clients}
    public_clients = [
        {key: value for key, value in client.items() if key != "token"}
        for client in clients
    ]
    policy_path.write_text(
        json.dumps({"schema_version": 1, "clients": public_clients}), encoding="utf-8"
    )
    return load_authorization_policy(str(policy_path), env=env)


def _operator_route(callback):
    return OperatorRoute("GET", "/v1/operator/workloads", WORKLOADS_READ, callback)


def _read_until_eof(sock: socket.socket) -> bytes:
    chunks = []
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


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


def test_v1_decisions_requires_auth():
    with running_server(StaticBackend(["ok"]), auth_token=TOKEN) as (host, port):
        status_unauthed, _, _ = _get(host, port, "/v1/decisions")
        status_authed, _, raw = _get(host, port, "/v1/decisions",
                                     headers={"x-api-key": TOKEN})
    assert status_unauthed == 401
    assert status_authed == 200
    assert json.loads(raw)["count"] == 0


def test_transition_boundary_requires_configured_auth_and_valid_token():
    backend = _TransitionBackend()
    with running_server(backend, auth_token=None) as (host, port):
        status_off, _, _ = _get(host, port, "/v1/admin/transition")
    with running_server(backend, auth_token=TOKEN) as (host, port):
        status_missing, _, _ = _get(host, port, "/v1/admin/transition")
        status_valid, _, raw = _get(
            host, port, "/v1/admin/transition?tier_id=primary-local",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert status_off == 404
    assert status_missing == 401
    assert status_valid == 200
    assert json.loads(raw)["tiers"][0]["expected_model"] == "heavy"


def test_transition_status_get_rejects_body_framing_and_closes_connection():
    backend = _TransitionBackend()
    with running_server(backend, auth_token=TOKEN) as (host, port):
        for _ in range(25):
            conn = http.client.HTTPConnection(host, port, timeout=5)
            try:
                conn.request(
                    "GET",
                    "/v1/admin/transition",
                    body=b"smuggled",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                response = conn.getresponse()
                raw = response.read()
                assert response.status == 400
                assert response.getheader("Connection") == "close"
                assert json.loads(raw)["error"]["type"] == "invalid_request"
            finally:
                conn.close()


def test_transition_mutations_preview_then_apply_and_drain():
    backend = _TransitionBackend()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with running_server(backend, auth_token=TOKEN) as (host, port):
        preview_status, _, preview_raw = _post(
            host, port, "/v1/admin/transition",
            {"action": "quiesce", "tier_id": "primary-local"}, headers,
        )
        assert backend.state == "admitting"
        applied_status, _, applied_raw = _post(
            host, port, "/v1/admin/transition",
            {
                "action": "quiesce", "tier_id": "primary-local",
                "confirm": True, "dry_run": False,
            }, headers,
        )
        drain_status, _, drain_raw = _post(
            host, port, "/v1/admin/transition",
            {"action": "drain", "tier_id": "primary-local", "timeout": 1}, headers,
        )
        readmit_status, _, _ = _post(
            host, port, "/v1/admin/transition",
            {
                "action": "readmit", "tier_id": "primary-local",
                "confirm": True, "dry_run": False,
            }, headers,
        )
    assert preview_status == 200
    assert json.loads(preview_raw)["applied"] is False
    assert applied_status == 200
    assert json.loads(applied_raw)["result"]["state"] == "quiesced"
    assert drain_status == 200
    assert json.loads(drain_raw)["result"]["drained"] is True
    assert readmit_status == 200
    assert backend.state == "admitting"


# --------------------------------------------------------------------------- #
# Scoped operator routes: distinct from ordinary router bearer authentication
# --------------------------------------------------------------------------- #
def test_operator_route_requires_exact_workloads_scope_and_preserves_legacy_auth(tmp_path):
    workloads_token = "workloads-token-12345"
    bootstrap_token = "bootstrap-token-12345"
    policy = _scoped_policy(tmp_path, [
        {"id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ", "token": workloads_token},
        {"id": "bootstrap", "scopes": [NODE_ADMIN_BOOTSTRAP], "credential_env": "BOOT", "token": bootstrap_token},
    ])
    calls = []
    route = _operator_route(lambda query: calls.append(query) or b'{"ok":true}')
    post_route = OperatorRoute(
        "POST", "/v1/operator/workloads", WORKLOADS_READ,
        lambda query: calls.append("post:" + query) or b'{"ok":true}',
    )
    with running_server(
        _TransitionBackend(), TOKEN, authorization_policy=policy,
        operator_routes=[route, post_route],
    ) as (host, port):
        missing, _, _ = _get(host, port, "/v1/operator/workloads")
        legacy, _, _ = _get(
            host, port, "/v1/operator/workloads", {"Authorization": f"Bearer {TOKEN}"}
        )
        bootstrap, _, _ = _get(
            host, port, "/v1/operator/workloads", {"Authorization": f"Bearer {bootstrap_token}"}
        )
        accepted, headers, raw = _get(
            host, port, "/v1/operator/workloads?raw=a%2Bb",
            {"Authorization": f"Bearer {workloads_token}"},
        )
        connection = http.client.HTTPConnection(host, port, timeout=5)
        connection.request(
            "POST", "/v1/operator/workloads", headers={
                "Authorization": f"Bearer {workloads_token}", "Content-Length": "0",
            },
        )
        post_response = connection.getresponse()
        post_status, post_raw = post_response.status, post_response.read()
        connection.close()
        chat, _, _ = _post(
            host, port, "/v1/chat/completions", _CHAT_BODY,
            {"Authorization": f"Bearer {workloads_token}"},
        )
        transition, _, _ = _get(
            host, port, TRANSITION_ENDPOINT,
            {"Authorization": f"Bearer {workloads_token}"},
        )
    assert (missing, legacy, bootstrap) == (403, 403, 403)
    assert accepted == 200
    assert headers["content-type"] == "application/json"
    assert headers["cache-control"] == "no-store"
    assert raw == b'{"ok":true}'
    assert (post_status, post_raw) == (200, b'{"ok":true}')
    assert calls == ["raw=a%2Bb", "post:"]
    assert chat == 401
    assert transition == 401


def test_operator_route_denial_precedes_unread_body_and_closes_socket(tmp_path):
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ",
        "token": "workloads-token-12345",
    }])
    calls = []
    post_route = OperatorRoute(
        "POST", "/v1/operator/workloads", WORKLOADS_READ, lambda query: b"{}"
    )
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}"), post_route],
    ) as (host, port):
        for method in ("GET", "POST"):
            with socket.create_connection((host, port), timeout=3) as raw_socket:
                raw_socket.sendall(
                    f"{method} /v1/operator/workloads HTTP/1.1\r\n".encode("ascii")
                    + b"Host: 127.0.0.1\r\n"
                    + b"Authorization: Bearer wrong-scoped-token\r\n"
                    + b"Content-Length: 9999999\r\n\r\n"
                )
                response = _read_until_eof(raw_socket)
            assert b"403" in response
            assert b"authorization_scope_denied" in response
            assert b"Connection: close" in response
    assert calls == []


@pytest.mark.parametrize(
    "credential_headers",
    [
        ("Authorization: Bearer {token}", "Authorization: Bearer {token}"),
        ("x-api-key: {token}", "x-api-key: {token}"),
        ("Authorization: Bearer {token}", "x-api-key: wrong-token"),
        ("x-api-key: {token}", "Authorization: Bearer wrong-token"),
    ],
)
def test_operator_route_rejects_ambiguous_credential_headers(
    tmp_path, credential_headers,
):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ",
        "token": token,
    }])
    calls = []
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        with socket.create_connection((host, port), timeout=3) as raw_socket:
            rendered = "\r\n".join(
                header.format(token=token) for header in credential_headers
            )
            raw_socket.sendall(
                b"GET /v1/operator/workloads HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + rendered.encode("ascii")
                + b"\r\nConnection: close\r\n\r\n"
            )
            response = _read_until_eof(raw_socket)
    assert b"403" in response
    assert b"authorization_scope_denied" in response
    assert b"Connection: close" in response
    assert calls == []


def test_operator_route_reauthenticates_each_keepalive_request(tmp_path):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ", "token": token,
    }])
    calls = []
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(
                "GET", "/v1/operator/workloads", headers={"Authorization": f"Bearer {token}"}
            )
            accepted = connection.getresponse()
            assert accepted.status == 200
            accepted.read()
            connection.request(
                "GET", "/v1/operator/workloads", headers={"Authorization": "Bearer wrong-token"}
            )
            rejected = connection.getresponse()
            assert rejected.status == 403
            assert rejected.getheader("Connection") == "close"
            rejected.read()
        finally:
            connection.close()
    assert calls == [""]


def test_operator_route_requires_bodyless_framing_and_safe_callback_output(tmp_path):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ", "token": token,
    }])
    headers = {"Authorization": f"Bearer {token}", "Content-Length": "1"}
    calls = []
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        framed, framed_headers, _ = _get(host, port, "/v1/operator/workloads", headers)
    assert framed == 400
    assert framed_headers["connection"] == "close"
    assert framed_headers["cache-control"] == "no-store"
    assert calls == []

    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        with socket.create_connection((host, port), timeout=3) as raw_socket:
            raw_socket.sendall(
                b"GET /v1/operator/workloads HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + f"Authorization: Bearer {token}\r\n".encode("ascii")
                + b"Content-Length: 0\r\nContent-Length: 0\r\n\r\n"
            )
            duplicate = _read_until_eof(raw_socket)
    assert b"400" in duplicate
    assert calls == []

    for callback in (
        lambda query: (_ for _ in ()).throw(RuntimeError("private callback detail")),
        lambda query: "not bytes",
        lambda query: b"x" * (8 * 1024 * 1024 + 1),
    ):
        with running_server(
            StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
            operator_routes=[_operator_route(callback)],
        ) as (host, port):
            status, _, raw = _get(
                host, port, "/v1/operator/workloads", {"Authorization": f"Bearer {token}"}
            )
        assert status == 500
        assert b"private callback detail" not in raw


def test_operator_registry_is_copied_bounded_and_collision_free(tmp_path):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ", "token": token,
    }])
    routes = [_operator_route(lambda query: b"{}")]
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy, operator_routes=routes
    ) as (host, port):
        routes.append(OperatorRoute("GET", "/v1/operator/mutated", WORKLOADS_READ, lambda query: b"{}"))
        status, _, _ = _get(
            host, port, "/v1/operator/mutated", {"Authorization": f"Bearer {token}"}
        )
    assert status == 401

    def callback(query):
        return b"{}"
    invalid = [
        OperatorRoute("DELETE", "/v1/operator/delete", WORKLOADS_READ, callback),
        OperatorRoute("GET", "/v1/chat/completions", WORKLOADS_READ, callback),
        OperatorRoute("GET", "/artifacts", WORKLOADS_READ, callback),
        OperatorRoute("GET", "/v1/requests/arbitrary", WORKLOADS_READ, callback),
        OperatorRoute("GET", "/v1/operator/trailing/", WORKLOADS_READ, callback),
        OperatorRoute("GET", "/v1/operator/bootstrap", NODE_ADMIN_BOOTSTRAP, callback),
    ]
    for route in invalid:
        with pytest.raises(ValueError):
            make_server("127.0.0.1", 0, StaticBackend(["ok"]), operator_routes=[route])
    with pytest.raises(ValueError):
        make_server(
            "127.0.0.1", 0, StaticBackend(["ok"]),
            operator_routes=[_operator_route(callback)] * 9,
        )

    class LyingRoutes(Sequence):
        def __len__(self):
            return 1

        def __getitem__(self, index):
            if index > 8:
                raise IndexError
            return OperatorRoute(
                "GET", f"/v1/operator/{index}", WORKLOADS_READ, callback
            )

    with pytest.raises(ValueError):
        make_server("127.0.0.1", 0, StaticBackend(["ok"]), operator_routes=LyingRoutes())
    with pytest.raises(ValueError):
        make_server(
            "127.0.0.1", 0, StaticBackend(["ok"]),
            operator_routes=[OperatorRoute([], "/v1/operator/bad", WORKLOADS_READ, callback)],
        )


def test_operator_route_requires_policy_even_when_legacy_auth_is_off():
    calls = []
    with running_server(
        StaticBackend(["ok"]), None,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        status, _, _ = _get(host, port, "/v1/operator/workloads")
    assert status == 403
    assert calls == []


def test_operator_route_capacity_rejects_without_callback(tmp_path, monkeypatch):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ", "token": token,
    }])
    blocked = threading.BoundedSemaphore(0)
    monkeypatch.setattr(front_door, "_OPERATOR_READ_LIMIT", blocked)
    calls = []
    with running_server(
        StaticBackend(["ok"]), TOKEN, authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    ) as (host, port):
        status, headers, _ = _get(
            host, port, "/v1/operator/workloads", {"Authorization": f"Bearer {token}"}
        )
    assert status == 503
    assert headers["cache-control"] == "no-store"
    assert calls == []


def test_operator_route_capacity_covers_response_delivery(tmp_path):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ",
        "token": token,
    }])
    calls = []
    calls_lock = threading.Lock()
    four_deliveries = threading.Event()
    fifth_delivery = threading.Event()
    release_deliveries = threading.Event()

    def callback(query):
        with calls_lock:
            calls.append(query)
        return b"{}"

    server = make_server(
        "127.0.0.1", 0, StaticBackend(["ok"]), auth_token=TOKEN,
        authorization_policy=policy, operator_routes=[_operator_route(callback)],
    )
    handler = server.RequestHandlerClass
    original_end_headers = handler.end_headers
    delivery_count = 0
    delivery_lock = threading.Lock()

    def blocked_end_headers(self):
        nonlocal delivery_count
        with delivery_lock:
            delivery_count += 1
            if delivery_count == 4:
                four_deliveries.set()
            elif delivery_count == 5:
                fifth_delivery.set()
        assert release_deliveries.wait(5)
        return original_end_headers(self)

    handler.end_headers = blocked_end_headers
    host, port = server.server_address[:2]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    results = []

    def request():
        results.append(_get(
            host, port, "/v1/operator/workloads",
            {"Authorization": f"Bearer {token}"},
        )[0])

    clients = [threading.Thread(target=request) for _ in range(4)]
    fifth = threading.Thread(target=request)
    try:
        for client in clients:
            client.start()
        assert four_deliveries.wait(5)
        fifth.start()
        assert fifth_delivery.wait(5)
        with calls_lock:
            assert len(calls) == 4
    finally:
        release_deliveries.set()
        for client in clients:
            client.join(timeout=5)
        fifth.join(timeout=5)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
    assert sorted(results) == [200, 200, 200, 200, 503]
    assert all(not client.is_alive() for client in [*clients, fifth])


def test_operator_route_write_failure_releases_capacity(tmp_path, monkeypatch):
    token = "workloads-token-12345"
    policy = _scoped_policy(tmp_path, [{
        "id": "reader", "scopes": [WORKLOADS_READ], "credential_env": "READ",
        "token": token,
    }])
    monkeypatch.setattr(front_door, "_OPERATOR_READ_LIMIT", threading.BoundedSemaphore(1))
    calls = []
    server = make_server(
        "127.0.0.1", 0, StaticBackend(["ok"]), auth_token=TOKEN,
        authorization_policy=policy,
        operator_routes=[_operator_route(lambda query: calls.append(query) or b"{}")],
    )
    handler = server.RequestHandlerClass
    original_end_headers = handler.end_headers
    fail_once = True

    class FailingWrite:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def write(self, payload):
            raise OSError("synthetic write failure")

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    def arm_failure(self):
        nonlocal fail_once
        original_end_headers(self)
        if fail_once:
            fail_once = False
            self.wfile = FailingWrite(self.wfile)

    handler.end_headers = arm_failure
    host, port = server.server_address[:2]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with socket.create_connection((host, port), timeout=3) as raw_socket:
            raw_socket.sendall(
                b"GET /v1/operator/workloads HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + f"Authorization: Bearer {token}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
            )
            _read_until_eof(raw_socket)
        status, _, _ = _get(
            host, port, "/v1/operator/workloads",
            {"Authorization": f"Bearer {token}"},
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
    assert status == 200
    assert calls == ["", ""]
