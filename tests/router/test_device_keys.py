"""Real HTTP checks for device policy, credential attribution, and revocation."""
from contextlib import contextmanager
import http.client
from itertools import count
import json
import threading

import pytest

from anvil_serving.router.config import ServerConfig
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
    # Join handlers, including post-response audit writes, before deleting SQLite.
    server.daemon_threads = False
    completions = {}
    handler = server.RequestHandlerClass

    class CompletedHandler(handler):
        def handle_one_request(self):
            try:
                super().handle_one_request()
            finally:
                headers = getattr(self, "headers", None)
                marker = headers.get("X-Test-Request-Completion") if headers else None
                completed = completions.get(marker)
                if completed is not None:
                    completed.set()

    server.RequestHandlerClass = CompletedHandler
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    connection._test_completions = completions
    connection._test_request_ids = count(1)
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
    # Only this fixture's sequential client awaits its own audit. Independent
    # clients in the saturation test keep their genuine concurrent behavior.
    completions = getattr(connection, "_test_completions", None)
    completed = threading.Event() if completions is not None else None
    marker = str(next(connection._test_request_ids)) if completed is not None else None
    if completed is not None:
        completions[marker] = completed
        headers["X-Test-Request-Completion"] = marker
    try:
        connection.request(method, path, payload, headers)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        if completed is not None:
            assert completed.wait(5), "request handler did not finish its credential audit"
        return result
    finally:
        if completions is not None:
            completions.pop(marker, None)


def test_device_catalog_sync_is_filtered_and_keeps_operator_routes_denied(store):
    from anvil_serving.client_catalog_sync import fetch_client_catalog, ClientCatalogError
    import copy
    meta, token = issue(store)
    with running(store) as (connection, backend):
        capabilities = {"object": "list", "data": [
            {"id": "private-tier", "model": "private-model", "aliases": ["llm.primary", "llm.private"],
             "context_limit_tokens": 327680, "limits": {"max_output_tokens": 65536, "images_per_request": 8},
             "modalities": ["text", "image"], "thinking": {"supported": True},
             "compat": {"supportedReasoningEfforts": ["low", "high"], "secret": "hidden"},
             "members": ["private-member"]},
            {"aliases": ["llm.private"], "model": "private-only"}]}
        queries = []
        def metadata(query):
            queries.append(query)
            return capabilities
        backend.model_capabilities = metadata
        backend.router_status = lambda: {"config_sha256": "a" * 64, "package_version": "1.5.0",
            "model_aliases": ["llm.primary", "llm.private"], "tier_counts": {"private": 2}}
        base = f"http://{connection.host}:{connection.port}/v1"
        catalog = fetch_client_catalog(base_url=base, environ={"ANVIL_ROUTER_TOKEN": token})
        assert list(catalog["models"]) == ["llm.primary"]
        assert catalog["config_sha256"] == "a" * 64
        for path in ("/v1/models/capabilities", "/v1/router/status"):
            status, _, body = request(connection, token, path, method="GET")
            assert status == 200 and b"private" not in body and b"hidden" not in body
        before = len(queries)
        assert request(connection, token, "/v1/models/capabilities?model=llm.private", method="GET")[0] == 404
        assert request(connection, token, "/v1/models/capabilities?model=unknown", method="GET")[0] == 404
        assert len(queries) == before
        for path in ("/v1/models/fingerprints", "/v1/models/capacity", "/v1/stats", "/v1/requests", "/metrics"):
            assert request(connection, token, path, method="GET")[0] == 403
        bad = copy.deepcopy(capabilities); bad["data"][0]["context_limit_tokens"] = False
        backend.model_capabilities = lambda _: bad
        with pytest.raises(ClientCatalogError):
            fetch_client_catalog(base_url=base, environ={"ANVIL_ROUTER_TOKEN": token})
        store.revoke(meta["key_id"])
        assert request(connection, token, "/v1/router/status", method="GET")[0] == 401
        assert backend.requests == []


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


@pytest.mark.parametrize("retired", ["revoked", "expired"])
@pytest.mark.parametrize("path", [CHAT, "/v1/models/capabilities", "/v1/router/status"])
def test_retired_device_namespace_cannot_fall_through_to_scoped_policy(store, tmp_path, monkeypatch, retired, path):
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
        assert request(connection, token, path, method="POST" if path == CHAT else "GET")[0] == 401
        assert backend.requests == []


def test_authentication_storage_work_is_bounded_and_master_remains_available(store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    _, token = issue(store)
    entered = threading.Barrier(5)
    release = threading.Event()
    original = KeyStore.authenticate

    def blocked_authenticate(self, candidate, **kwargs):
        entered.wait(5)
        assert release.wait(5)
        return original(self, candidate, **kwargs)

    monkeypatch.setattr(KeyStore, "authenticate", blocked_authenticate)
    # This gate tests bounded authentication work. SQLite contention can
    # independently return 503 after one second and has its own storage tests.
    monkeypatch.setattr(KeyStore, "admit", lambda self, key_id: 0)
    monkeypatch.setattr(KeyStore, "record", lambda *args: None)
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
