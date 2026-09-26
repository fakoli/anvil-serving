"""Real HTTP checks for device policy, credential attribution, and revocation."""
from contextlib import contextmanager
import http.client
import json
import threading

import pytest

from anvil_serving.router.config import ServerConfig, ConfigError, load_server_config
from anvil_serving.router.front_door import make_server
from anvil_serving.router.keys import KeyStore
from tests.router.helpers import StaticBackend
from tests.router.key_fixtures import tmp_path as tmp_path


MASTER = "test-master-credential"
CHAT = "/v1/chat/completions"


class RecordingBackend(StaticBackend):
    def __init__(self):
        super().__init__(["ok"])
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return super().generate(request)


@pytest.fixture
def store(tmp_path):
    return KeyStore.initialize(tmp_path / "credentials" / "keys.sqlite3")


def issue(store, *, rpm=100, paths=None, models=None):
    return store.create("test-device", models=models or ["llm.primary"],
                        paths=paths or [CHAT], rpm=rpm)


@contextmanager
def running(store, **kwargs):
    backend = RecordingBackend()
    server = make_server("127.0.0.1", 0, backend, auth_token=MASTER,
        model_routes=["llm.primary", "llm.private"],
        server_config=ServerConfig(api_keys_path=str(store.path)), **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        yield connection, backend
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


def request(connection, token, path=CHAT, body=None, method="POST", extra=None):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    headers.update(extra or {})
    payload = None if method == "GET" else json.dumps(body if body is not None else {
        "model": "llm.primary", "messages": [{"role": "user", "content": "private prompt"}]})
    connection.request(method, path, payload, headers)
    response = connection.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def test_catalog_grants_denials_and_master(store):
    meta, token = issue(store)
    with running(store) as (connection, backend):
        status, _, raw = request(connection, token, "/v1/models", method="GET")
        assert status == 200
        assert [x["id"] for x in json.loads(raw)["data"]] == ["llm.primary"]
        assert request(connection, token, body={"model": "llm.private", "messages": []})[0] == 403
        for method, path in [("GET", "/v1/models/capacity"), ("GET", "/v1/requests"),
            ("POST", "/v1/admin/transition"), ("POST", "/mcp"),
            ("POST", "/v1/audio/speech"), ("GET", "/metrics"), ("DELETE", CHAT)]:
            assert request(connection, token, path, method=method)[0] == 403
        assert backend.requests == []
        assert request(connection, MASTER, "/v1/models", method="GET")[0] == 200
        assert request(connection, token + "wrong")[0] == 401
        assert request(connection, token, "/healthz", method="GET")[0] == 200


@pytest.mark.parametrize("path,body", [
    (CHAT, {"model": " LLM.PRIMARY ", "messages": [{"role": "user", "content": "hi"}]}),
    ("/v1/messages", {"model": "llm.primary", "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]}),
    ("/v1/responses", {"model": "llm.primary", "input": "hi"}),
])
def test_dialects_streaming_and_trusted_identity(store, path, body):
    meta, token = issue(store, paths=[path])
    with running(store) as (connection, backend):
        for stream in (False, True):
            spoofed = {} if path == "/v1/responses" else {"_anvil_correlation": {"client_id": "spoof"}}
            status, headers, raw = request(connection, token, path + "/?ignored=true",
                {**body, "stream": stream, **spoofed},
                extra={"X-Anvil-Client-Id": "spoof"})
            assert status == 200
            assert b"ok" in raw
            assert backend.requests[-1].raw["_anvil_correlation"]["client_id"] == meta["key_id"]
            assert backend.requests[-1].raw["_anvil_correlation"]["gateway_request_id"] == headers["X-Anvil-Request-Id"]
    history = store.usage(meta["key_id"])
    rendered = json.dumps(history)
    assert meta["key_id"] in rendered
    assert token not in rendered and "spoof" not in rendered and "ignored" not in rendered


def test_revocation_on_reused_connection_and_separate_budgets(store):
    first, token = issue(store, rpm=1)
    _, second = issue(store, rpm=1)
    with running(store) as (connection, backend):
        assert request(connection, token)[0] == 200
        status, headers, _ = request(connection, token)
        assert status == 429 and int(headers["Retry-After"]) > 0
        assert request(connection, second)[0] == 200
        store.revoke(first["key_id"])
        assert request(connection, token)[0] == 401
        assert request(connection, MASTER)[0] == 200
        assert len(backend.requests) == 3


def test_ambiguous_headers_and_storage_failure_close_without_dispatch(store):
    _, token = issue(store)
    with running(store) as (connection, backend):
        assert request(connection, token, extra={"x-api-key": MASTER})[0] == 401
        store.path.unlink()
        assert request(connection, token)[0] == 503
        assert request(connection, MASTER)[0] == 200
        assert len(backend.requests) == 1


def test_config_requires_master(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text('[server]\napi_keys_path = ' + json.dumps(str(tmp_path / "keys.sqlite3")) + "\n")
    with pytest.raises(ConfigError, match="auth_env"):
        load_server_config(str(config))


@pytest.mark.parametrize("kind,path,fields", [
    ("embedding", "/v1/embeddings", {"input": "private prompt"}),
    ("rerank", "/v1/rerank", {"query": "private prompt", "documents": ["text"]}),
])
def test_purpose_model_grants_and_upstream_correlation(store, kind, path, fields):
    from anvil_serving.router.config import PurposeModel
    from anvil_serving.router.purpose import PurposeRouter
    from anvil_serving.router.decision_log import DecisionLog

    calls = []
    decisions = DecisionLog()

    def transport(url, **kwargs):
        calls.append(kwargs)
        return b'{"data":[],"usage":{"prompt_tokens":3}}'

    purpose = PurposeRouter([
        PurposeModel("embed", kind, "Org/Embed", "http://127.0.0.1:9/v1"),
        PurposeModel("private", kind, "private-model", "http://127.0.0.1:9/v1"),
    ], transport=transport, decision_log=decisions)
    meta, token = issue(store, paths=[path], models=["Org/Embed", "retired-model"])
    with running(store, purpose=purpose) as (connection, backend):
        status, headers, raw = request(connection, token, path,
            {"model": "Org/Embed", **fields, "_anvil_correlation": {"client_id": "spoof"}})
        assert status == 200
        assert len(calls) == 1 and backend.requests == []
        assert calls[0]["headers"]["X-Request-Id"] == headers["X-Anvil-Request-Id"]
        assert token not in json.dumps(calls[0]["headers"])
        assert "_anvil_correlation" not in json.loads(calls[0]["data"])
        assert decisions.records[0].client_id == meta["key_id"]
        assert decisions.records[0].total_prompt_tokens == 3
        for model in ("private-model", "org/embed"):
            assert request(connection, token, path, {"model": model, **fields})[0] == 403
        status, _, raw = request(connection, token, path, {"model": "retired-model", **fields})
        assert status == 404 and b"private-model" not in raw
    assert token not in json.dumps(store.usage())


def test_single_api_header_and_health_does_not_displace_usage(store):
    meta, token = issue(store)
    with running(store) as (connection, backend):
        connection.request("GET", "/v1/models", headers={"x-api-key": token})
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        for _ in range(3):
            assert request(connection, "invalid", "/healthz", method="GET")[0] == 200
            assert request(connection, "invalid")[0] == 401
    history = store.usage()
    assert len(history) == 1 and history[0]["key_id"] == meta["key_id"]


def test_scoped_operator_credential_independent_of_device_store(store, tmp_path):
    from anvil_serving.control_plane.authorization import load_authorization_policy
    policy_file = tmp_path / "policy.json"
    scoped = "test-scoped-inference-credential"
    policy_file.write_text(json.dumps({"schema_version": 1, "clients": [{
        "id": "existing-client", "scopes": ["inference:use"], "credential_env": "TEST_SCOPED",
    }]}))
    policy = load_authorization_policy(str(policy_file), env={"TEST_SCOPED": scoped})
    _, device = issue(store)
    with running(store, authorization_policy=policy) as (connection, backend):
        store.path.unlink()
        assert request(connection, scoped)[0] == 200
        assert request(connection, device)[0] == 503
        assert backend.requests[0].raw["_anvil_correlation"]["client_id"] == "existing-client"


def test_config_rejects_ambiguous_relative_key_store(tmp_path):
    config = tmp_path / "router.toml"
    config.write_text('[server]\nauth_env="TEST_MASTER"\napi_keys_path="keys.sqlite3"\n')
    with pytest.raises(ConfigError, match="absolute"):
        load_server_config(str(config))


@pytest.mark.parametrize("retired", ["revoked", "expired"])
def test_retired_device_namespace_cannot_fall_through_to_scoped_policy(store, tmp_path, monkeypatch, retired):
    from anvil_serving.control_plane.authorization import load_authorization_policy
    from anvil_serving.router import keys

    meta, token = issue(store)
    policy_file = tmp_path / "collision-policy.json"
    policy_file.write_text(json.dumps({"schema_version": 1, "clients": [{
        "id": "existing-client", "scopes": ["inference:use"], "credential_env": "TEST_SCOPED",
    }]}))
    policy = load_authorization_policy(str(policy_file), env={"TEST_SCOPED": token})
    if retired == "revoked":
        store.revoke(meta["key_id"])
    else:
        with store._connect() as connection:
            connection.execute("UPDATE keys SET expires_at = 1")
        monkeypatch.setattr(keys.time, "time", lambda: 2)
    with running(store, authorization_policy=policy) as (connection, backend):
        assert request(connection, token)[0] == 401
        assert backend.requests == []


def test_authentication_storage_work_is_bounded_and_master_remains_available(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    _, token = issue(store)
    entered = threading.Barrier(5)
    release = threading.Event()
    original = KeyStore.authenticate

    def blocked_authenticate(self, candidate):
        entered.wait(5)
        assert release.wait(5)
        return original(self, candidate)

    monkeypatch.setattr(KeyStore, "authenticate", blocked_authenticate)
    with running(store) as (connection, backend), ThreadPoolExecutor(max_workers=4) as pool:
        def send():
            client = http.client.HTTPConnection(connection.host, connection.port, timeout=5)
            try:
                return request(client, token)[0]
            finally:
                client.close()

        futures = [pool.submit(send) for _ in range(4)]
        try:
            entered.wait(5)
            assert request(connection, token)[0] == 503
            assert request(connection, MASTER)[0] == 200
        finally:
            release.set()
        assert [future.result() for future in futures] == [200] * 4
