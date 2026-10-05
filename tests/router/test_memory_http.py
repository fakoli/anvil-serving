"""Real HTTP checks for the principal-bound supplemental-memory surface."""

from contextlib import contextmanager
import http.client
import json
import threading

from anvil_serving.router.config import MemoryRoute, ServerConfig
from anvil_serving.router.front_door import MEMORY_MCP_PATH, MEMORY_PATH, make_server
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.memory import MemoryRouter
from tests.router.helpers import StaticBackend
from tests.router.key_fixtures import tmp_path as tmp_path


MASTER = "test-master-credential"
ALIAS = "memory.main"


@contextmanager
def server(tmp_path, *, models=None):
    store = KeyStore.initialize(tmp_path / "keys.sqlite3")
    metadata, key = store.create("device", models=[ALIAS] if models is None else models, paths=[MEMORY_PATH, MEMORY_MCP_PATH])
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return b'{"ok":true,"results":[]}'

    memory = MemoryRouter((MemoryRoute(ALIAS, metadata["key_id"], "hindsight", "bank", "http://127.0.0.1:9010", "MEMORY_TOKEN"),),
                          env={"MEMORY_TOKEN": "upstream-token"}, transport=transport)
    httpd = make_server("127.0.0.1", 0, StaticBackend(["unused"]), auth_token=MASTER,
                        memory=memory, server_config=ServerConfig(api_keys_path=str(store.path)))
    # The front door uses daemon request threads in production so an abandoned
    # keep-alive connection cannot delay shutdown.  These tests close every
    # client connection, so wait for their final credential-audit writes before
    # the Windows-private temporary database is removed.
    httpd.daemon_threads = False
    request_complete = threading.Event()
    handler = httpd.RequestHandlerClass

    class CompletedHandler(handler):
        def handle(self):
            try:
                super().handle()
            finally:
                request_complete.set()

    httpd.RequestHandlerClass = CompletedHandler
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield (httpd.server_address, request_complete), key, calls
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


def request(address, token, path, body, headers=None):
    socket_address, request_complete = address
    request_complete.clear()
    connection = http.client.HTTPConnection(*socket_address, timeout=5)
    request_headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Connection": "close"}
    request_headers.update(headers or {})
    try:
        connection.request("POST", path, json.dumps(body), request_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b"{}")
    finally:
        connection.close()
        # A complete HTTP body precedes credential-audit cleanup. These are
        # sequential protocol tests, so await the one-shot handler, not just
        # response bytes, before allowing another request to consume a slot.
        assert request_complete.wait(5), "request handler did not finish its credential audit"


def current_request(request_id, method, params):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {
        **params, "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}}}


def test_request_waits_for_post_response_credential_audit(tmp_path, monkeypatch):
    audit_started = threading.Event()
    release_audit = threading.Event()
    body_read = threading.Event()
    awaiting_completion = threading.Event()
    request_returned = threading.Event()
    outcomes = []
    record = KeyStore.record
    read = http.client.HTTPResponse.read

    def held_record(self, *args, **kwargs):
        audit_started.set()
        assert release_audit.wait(5)
        return record(self, *args, **kwargs)

    def observed_read(self, *args, **kwargs):
        result = read(self, *args, **kwargs)
        body_read.set()
        return result

    monkeypatch.setattr(KeyStore, "record", held_record)
    monkeypatch.setattr(http.client.HTTPResponse, "read", observed_read)
    with server(tmp_path) as (address, key, _calls):
        completion_wait = address[1].wait

        def observed_completion_wait(timeout):
            awaiting_completion.set()
            return completion_wait(timeout)

        monkeypatch.setattr(address[1], "wait", observed_completion_wait)

        def invoke():
            try:
                outcomes.append(request(address, key, MEMORY_MCP_PATH, {},
                                        {"MCP-Protocol-Version": "2025-11-25"}))
            finally:
                request_returned.set()

        client = threading.Thread(target=invoke)
        client.start()
        try:
            assert audit_started.wait(5) and body_read.wait(5) and awaiting_completion.wait(5)
            assert not request_returned.is_set()
        finally:
            release_audit.set()
            client.join(5)
        assert not client.is_alive() and request_returned.is_set()
        assert outcomes == [(200, {"jsonrpc": "2.0", "id": None,
                                  "error": {"code": -32600, "message": "invalid JSON-RPC request"}})]


def test_memory_http_is_device_bound_and_routes_only_the_configured_bank(tmp_path):
    with server(tmp_path) as (address, key, calls):
        status, payload = request(address, key, MEMORY_PATH, {"alias": ALIAS, "operation": "retain", "arguments": {"content": "remember this"}})
        assert status == 200 and payload["backend"] == "hindsight"
        assert calls[0][0] == "http://127.0.0.1:9010/v1/default/banks/bank/memories"
        assert calls[0][1]["headers"]["Authorization"] == "Bearer upstream-token"
        assert request(address, MASTER, MEMORY_PATH, {"alias": ALIAS, "operation": "recall", "arguments": {"query": "x"}})[0] == 403
        status, payload = request(address, key, MEMORY_PATH, {"alias": "missing", "operation": "recall", "arguments": {"query": "x"}})
        assert status == 404 and payload["error"]["type"] == "memory_not_found"
        assert request(address, key, MEMORY_PATH, {"alias": ALIAS, "operation": "recall", "arguments": {"query": "x"}}, {"X-Bank-Id": "other"})[0] == 400


def test_memory_mcp_is_stateless_and_never_executes_tool_notifications(tmp_path):
    with server(tmp_path) as (address, key, calls):
        status, listed = request(address, key, MEMORY_MCP_PATH, current_request(1, "tools/list", {}),
                                 {"MCP-Protocol-Version": "2026-07-28"})
        assert status == 200
        assert {tool["name"] for tool in listed["result"]["tools"]} == {"memory_retain", "memory_recall", "memory_reflect"}
        assert {tool["name"]: tool["annotations"]["readOnlyHint"] for tool in listed["result"]["tools"]} == {
            "memory_retain": False, "memory_recall": True, "memory_reflect": True,
        }
        call = current_request(2, "tools/call", {"name": "memory_recall", "arguments": {"alias": ALIAS, "query": "find"}})
        for headers in ({}, {"MCP-Protocol-Version": "2025-11-25"}):
            response = request(address, key, MEMORY_MCP_PATH, call, headers)[1]["result"]
            assert response["isError"] is False
            assert "structuredContent" not in response
            assert json.loads(response["content"][0]["text"])["result"]["results"] == []
        before = len(calls)
        notification = current_request(3, "tools/call", {"name": "memory_retain", "arguments": {"alias": ALIAS, "content": "nope"}})
        notification.pop("id")
        assert request(address, key, MEMORY_MCP_PATH, notification)[0] == 202
        assert len(calls) == before
        legacy = {"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
        assert request(address, key, MEMORY_MCP_PATH, legacy)[1]["result"]["protocolVersion"] == "2025-11-25"


def test_normalized_alias_cannot_bypass_device_model_grant(tmp_path):
    with server(tmp_path, models=["llm.primary"]) as (address, key, calls):
        for alias in (ALIAS, ALIAS.upper(), " " + ALIAS.upper() + " "):
            body = {"alias": alias, "operation": "recall", "arguments": {"query": "find"}}
            status, error = request(address, key, MEMORY_PATH, body)
            assert status == 403 and error["error"]["type"] == "key_access_denied"
            for headers in ({"MCP-Protocol-Version": "2025-11-25"}, {}):
                call = current_request(1, "tools/call", {"name": "memory_recall", "arguments": {"alias": alias, "query": "find"}})
                if headers:
                    call["params"].pop("_meta")
                status, result = request(address, key, MEMORY_MCP_PATH, call, headers)
                assert status == 200 and result["result"]["isError"] is True
                assert json.loads(result["result"]["content"][0]["text"])["code"] == "key_access_denied"
        assert calls == []


def test_mcp_rejects_malformed_envelopes_before_notification_suppression(tmp_path):
    with server(tmp_path) as (address, key, calls):
        base = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        invalid = [{}, {"method": "tools/list"}, {**base, "jsonrpc": "1.0"},
                   {**base, "method": []}, {**base, "params": []}]
        invalid += [{**base, "id": value} for value in ({}, [], None, True, 1.5)]
        for body in invalid:
            status, result = request(address, key, MEMORY_MCP_PATH, body,
                                     {"MCP-Protocol-Version": "2025-11-25"})
            assert status == 200 and result["error"]["code"] == -32600
        assert calls == []
