from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import threading

import pytest

from anvil_serving.native_agents.auth import AdmissionDenied, NativeAuthenticator
from anvil_serving.native_agents.types import NativeAdmission
from anvil_serving.native_memory_gateway import server as gateway_server
from anvil_serving.native_memory_gateway.server import (
    CAPABILITIES,
    BoundedServer,
    GatewayError,
    NativeMemoryGateway,
    _memory_credential,
    authentication_reader,
    credential_path,
    handler,
    router_request,
    read_systemd_credential,
    validate_config,
)


PRINCIPAL = "human:" + "a" * 64
OTHER = "human:" + "b" * 64
SECRET = "ask_" + "A" * 43
NOW = 1_800_000_000


def auth_config():
    return {
        "issuer": "https://identity.example.test",
        "introspection_endpoint": "https://identity.example.test/api/oidc/introspection",
        "userinfo_endpoint": "https://identity.example.test/api/oidc/userinfo",
        "introspection_client_id": "native-memory-introspection",
        "secret_file": "/run/credentials/native-memory/introspection-secret",
        "native_client_ids": ["anvil-ios"],
        "authority_file": "/run/anvil-connect/native-agents-authority.json",
        "resource": "native-agents",
        "source_resource": "pi",
    }


def config(**changes):
    value = {
        "version": 1,
        "deployment_id": "iphone",
        "listen_port": 9443,
        "expected_principal": PRINCIPAL,
        "credential_name": "memory-key",
        "credential_sha256": "f" * 64,
        "router_origin": "http://127.0.0.1:8000",
        "timeout_seconds": 120,
        "auth": auth_config(),
    }
    value.update(changes)
    return value


def receipt(**changes):
    value = {
        "schema": "anvil-user-memory-key/v1",
        "principal": PRINCIPAL,
        "generation": "7",
        "epoch": "c" * 64,
        "key_id": "key_" + "d" * 16,
        "secret": SECRET,
        "expires_at": NOW + 3600,
        "previous": None,
    }
    value.update(changes)
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


class Auth:
    def __init__(self, principal=PRINCIPAL, generation=7):
        self.admission = NativeAdmission(principal, generation, NOW + 1800, credential_id="digest")
        self.allowed = True
        self.calls = []

    def authorize(self, token):
        self.calls.append(token)
        if not self.allowed:
            raise AdmissionDenied()
        return self.admission

    def recheck(self, admission):
        return self.allowed and admission == self.admission


def gateway(*, principal=PRINCIPAL, generation=7, raw=None, transport=None):
    raw = receipt() if raw is None else raw
    auth = Auth(principal, generation)
    calls = []

    def forward(origin, body, secret, timeout):
        calls.append((origin, json.loads(body), secret, timeout))
        return (200, {"schema": "anvil-native-memory/v1", "result": {"ok": True}})

    cfg = config(credential_sha256=hashlib.sha256(raw).hexdigest())
    instance = NativeMemoryGateway(cfg, auth, Path("/run/credentials/unit/memory-key"),
                                   read=lambda *_: raw, transport=transport or forward, clock=lambda: NOW)
    return instance, auth, calls


def test_config_is_closed_and_router_is_exact_loopback():
    validated = validate_config(config())
    assert validated["router_origin"] == "http://127.0.0.1:8000"
    assert type(validated["auth"]["native_client_ids"]) is list
    assert NativeAuthenticator(validated["auth"]).config["native_client_ids"] == ("anvil-ios",)
    for change in (
        {"router_origin": "https://router.example.test:443"},
        {"router_origin": "http://localhost:8000"},
        {"router_origin": "http://127.0.0.1:8000/v1/memory"},
        {"router_origin": "http://user@127.0.0.1:8000"},
        {"credential_name": "other"},
        {"expected_principal": OTHER.upper()},
        {"timeout_seconds": True},
    ):
        with pytest.raises(ValueError):
            validate_config(config(**change))
    with pytest.raises(ValueError):
        validate_config({**config(), "extra": True})


def test_load_config_to_serve_constructs_live_auth_once(tmp_path, monkeypatch):
    raw_config = json.dumps(config(credential_sha256=hashlib.sha256(receipt()).hexdigest())).encode()
    config_path = tmp_path / "config.json"
    config_path.write_bytes(raw_config)
    fake = SimpleNamespace(serve_forever=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(gateway_server, "protected_read", lambda path, _maximum: raw_config)
    monkeypatch.setattr(gateway_server, "credential_path", lambda *_args: Path("/run/credentials/unit/memory-key"))
    monkeypatch.setattr(gateway_server, "read_systemd_credential", lambda *_args: receipt())
    monkeypatch.setattr(gateway_server, "BoundedServer", lambda *_args, **_kwargs: fake)
    gateway_server.serve(config_path)


def test_systemd_credential_name_cannot_select_an_arbitrary_file():
    assert credential_path("memory-key", "iphone", {"CREDENTIALS_DIRECTORY": "/run/credentials/anvil-native-memory-iphone.service"}) == Path(
        "/run/credentials/anvil-native-memory-iphone.service/memory-key"
    )
    for name, deployment, environ in (
        ("other", "iphone", {"CREDENTIALS_DIRECTORY": "/run/credentials/anvil-native-memory-iphone.service"}),
        ("memory-key", "other", {"CREDENTIALS_DIRECTORY": "/run/credentials/anvil-native-memory-iphone.service"}),
        ("memory-key", "iphone", {}),
        ("memory-key", "iphone", {"CREDENTIALS_DIRECTORY": "relative"}),
    ):
        with pytest.raises(ValueError):
            credential_path(name, deployment, environ)


def test_systemd_credential_reader_requires_read_only_exact_custody(tmp_path, monkeypatch):
    source = tmp_path / "memory-key"
    source.write_bytes(b"protected-receipt")
    source.chmod(0o400)
    target = Path("/run/credentials/anvil-native-memory-iphone.service/memory-key")
    source_info = source.stat()
    real_open = __import__("os").open

    def metadata(path):
        path = Path(path)
        if path == target.parent:
            return SimpleNamespace(st_mode=0o40500, st_uid=__import__("os").geteuid(),
                                   st_gid=__import__("os").getegid())
        return SimpleNamespace(st_mode=0o40755, st_uid=0, st_gid=0)

    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(Path, "stat", lambda path: SimpleNamespace(st_dev=source_info.st_dev))
    monkeypatch.setattr("os.open", lambda _path, flags: real_open(source, flags))
    monkeypatch.setattr("os.statvfs", lambda _path: SimpleNamespace(f_flag=getattr(__import__("os"), "ST_RDONLY", 1)))
    assert read_systemd_credential(target, 4096) == b"protected-receipt"
    monkeypatch.setattr("os.statvfs", lambda _path: SimpleNamespace(f_flag=0))
    with pytest.raises(AdmissionDenied):
        read_systemd_credential(target, 4096)


def test_authentication_reader_uses_service_credential_only_for_exact_introspection_leaf():
    cfg = config()
    target = Path(cfg["auth"]["secret_file"])
    calls = []
    read = authentication_reader(
        cfg, target,
        credential_read=lambda path, maximum: calls.append(("credential", path, maximum)) or b"secret",
        authority_read=lambda path, maximum: calls.append(("authority", path, maximum)) or b"authority",
    )
    assert read(str(target), 8192) == b"secret"
    assert read(cfg["auth"]["authority_file"], 65536) == b"authority"
    assert read(Path(target), 4096) == b"authority"
    assert calls == [
        ("credential", target, 4096),
        ("authority", cfg["auth"]["authority_file"], 65536),
        ("authority", target, 4096),
    ]


def test_receipt_requires_current_complete_key_and_retired_previous():
    raw = receipt(previous={"key_id": "key_" + "e" * 16, "secret": "ask_" + "B" * 43, "retired": True})
    assert _memory_credential(raw, hashlib.sha256(raw).hexdigest(), NOW)["secret"] == SECRET
    invalid = (
        receipt(key_id=None),
        receipt(expires_at=NOW),
        receipt(previous={"key_id": "key_" + "e" * 16, "secret": "ask_" + "B" * 43, "retired": False}),
        receipt(previous={"key_id": "key_" + "e" * 16, "secret": SECRET, "retired": True}),
    )
    for value in invalid:
        with pytest.raises(GatewayError) as failure:
            _memory_credential(value, hashlib.sha256(value).hexdigest(), NOW)
        assert (failure.value.status, failure.value.code) == (503, "memory_unavailable")
    with pytest.raises(GatewayError):
        _memory_credential(raw, "0" * 64, NOW)


def test_capabilities_are_exact_and_do_not_expose_router_key_metadata():
    instance, auth, _calls = gateway()
    assert instance.capabilities("provider-token") == {
        "version": 1,
        "owner_id": PRINCIPAL,
        "capabilities": list(CAPABILITIES),
    }
    assert auth.calls == ["provider-token"]
    assert SECRET not in json.dumps(instance.capabilities("second-token"))


def test_oidc_principal_and_generation_must_match_receipt_before_router_call():
    for principal, generation in ((OTHER, 7), (PRINCIPAL, 8)):
        instance, _auth, calls = gateway(principal=principal, generation=generation)
        with pytest.raises(GatewayError) as failure:
            instance.memory("token", {"alias": "memory.user", "operation": "detail", "arguments": {"id": "x"}})
        assert (failure.value.status, failure.value.code) == (403, "memory_forbidden")
        assert calls == []

    instance, auth, calls = gateway()
    auth.admission = NativeAdmission(PRINCIPAL, 7, NOW + 1800, resource="other", credential_id="digest")
    with pytest.raises(GatewayError) as failure:
        instance.capabilities("token")
    assert (failure.value.status, failure.value.code) == (403, "memory_forbidden") and calls == []


def test_native_envelope_forwards_only_exact_memory_surface_and_server_credential():
    instance, _auth, calls = gateway()
    body = {"alias": "memory.user", "operation": "banks", "arguments": {
        "native": True, "expected_principal": PRINCIPAL, "limit": 100, "offset": 0,
    }}
    assert instance.memory("provider-token", body)[0] == 200
    assert calls == [("http://127.0.0.1:8000", body, SECRET, 120)]

    denied = (
        {"alias": "memory.user", "operation": "retain", "arguments": {"content": "no"}},
        {"alias": "memory.user", "operation": "banks", "arguments": {"native": True, "expected_principal": OTHER}},
        {"alias": "memory.user", "operation": "detail", "bank": "../other", "arguments": {"id": "x"}},
        {"alias": "other", "operation": "detail", "arguments": {"id": "x"}},
        {"alias": "memory.user", "operation": "detail", "arguments": {"id": "x"}, "extra": True},
    )
    for value in denied:
        with pytest.raises(GatewayError):
            instance.memory("provider-token", value)
    assert len(calls) == 1


def test_revoked_oidc_after_router_response_discards_result():
    instance, auth, calls = gateway()

    def revoke(*args):
        result = (200, {"private": "memory"})
        auth.allowed = False
        return result

    instance.transport = revoke
    with pytest.raises(GatewayError) as failure:
        instance.memory("token", {"alias": "memory.user", "operation": "detail", "arguments": {"id": "x"}})
    assert (failure.value.status, failure.value.code) == (401, "unauthorized")
    assert calls == []


def test_receipt_change_during_request_discards_result():
    first = receipt()
    second = receipt(expires_at=NOW + 7200)
    reads = iter((first, second))
    cfg = config(credential_sha256=hashlib.sha256(first).hexdigest())
    instance = NativeMemoryGateway(cfg, Auth(), Path("/run/credentials/unit/memory-key"), read=lambda *_: next(reads),
        transport=lambda *_: (200, {"private": "memory"}), clock=lambda: NOW)
    with pytest.raises(GatewayError) as failure:
        instance.memory("token", {"alias": "memory.user", "operation": "detail", "arguments": {"id": "x"}})
    assert failure.value.code == "memory_unavailable"


@contextmanager
def http_gateway(instance):
    server = BoundedServer(("127.0.0.1", 0), handler(instance))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def request(address, method, path, *, token="provider-token", body=None, headers=None):
    connection = http.client.HTTPConnection(*address, timeout=5)
    raw = None if body is None else json.dumps(body)
    values = {"Authorization": "Bearer " + token, "Connection": "close", **(headers or {})}
    if raw is not None:
        values["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=raw, headers=values)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        connection.close()


def test_http_contract_is_exact_and_never_reflects_token():
    instance, auth, _calls = gateway()
    with http_gateway(instance) as address:
        status, value = request(address, "GET", "/v1/native/memory/capabilities", token="private-provider-token")
        assert status == 200 and value["owner_id"] == PRINCIPAL
        assert "private-provider-token" not in json.dumps(value)
        assert request(address, "GET", "/v1/native/memory/capabilities?x=1")[0] == 404
        assert request(address, "POST", "/v1/native/memory/capabilities", body={})[0] == 404
        assert request(address, "PUT", "/v1/memory", body={})[0] == 405
        assert request(address, "POST", "/v1/memory", body={"alias": "memory.user", "operation": "detail", "arguments": {"id": "x"}})[0] == 200
        auth.allowed = False
        assert request(address, "GET", "/v1/native/memory/capabilities")[0] == 401


def test_dripped_headers_have_an_absolute_deadline_and_release_connection_slot():
    instance, _auth, _calls = gateway()
    server = BoundedServer(("127.0.0.1", 0), handler(instance, header_timeout=0.15), maximum_connections=1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stalled = socket.create_connection(server.server_address, timeout=2)
    try:
        stalled.sendall(b"GET /v1/native/memory/capabilities HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Drip:")
        threading.Event().wait(0.3)
        assert request(server.server_address, "GET", "/v1/native/memory/capabilities")[0] == 200
    finally:
        stalled.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


@contextmanager
def router(response_status=200, response_body=b'{"ok":true}', content_type="application/json"):
    seen = []

    class Router(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            size = int(self.headers["Content-Length"])
            seen.append((self.path, dict(self.headers), self.rfile.read(size)))
            self.send_response(response_status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Router)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_router_transport_strips_caller_headers_and_maps_server_key_401():
    with router() as (origin, seen):
        assert router_request(origin, b'{"safe":true}', SECRET, 3) == (200, {"ok": True})
        path, headers, body = seen[0]
        assert path == "/v1/memory" and body == b'{"safe":true}'
        assert headers["Authorization"] == "Bearer " + SECRET
        assert set(headers).isdisjoint({"X-Principal", "Cookie", "X-Api-Key"})
    with router(401, b'{"error":{"code":"invalid_api_key"}}') as (origin, _seen):
        with pytest.raises(GatewayError) as failure:
            router_request(origin, b"{}", SECRET, 3)
        assert (failure.value.status, failure.value.code) == (503, "memory_unavailable")


@pytest.mark.parametrize("status,body,content_type", [
    (302, b'{"redirect":true}', "application/json"),
    (200, b"not-json", "application/json"),
    (200, b'{"ok":true}', "text/plain"),
])
def test_router_transport_fails_closed_on_redirect_or_invalid_response(status, body, content_type):
    with router(status, body, content_type) as (origin, _seen):
        with pytest.raises(GatewayError) as failure:
            router_request(origin, b"{}", SECRET, 3)
        assert failure.value.code == "memory_unavailable"


def test_router_transport_rejects_response_over_two_mib():
    with router(200, b"{" + b" " * (2 * 1024 * 1024) + b"}") as (origin, _seen):
        with pytest.raises(GatewayError) as failure:
            router_request(origin, b"{}", SECRET, 3)
        assert failure.value.code == "memory_unavailable"
