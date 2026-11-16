"""Tests for the ADR-0014 controller transport.

Hermetic: live HTTP tests bind only to 127.0.0.1:0 with fake MCP functions.
"""

import contextlib
import http.client
import json
import os
import socket
import tempfile
import threading
import time

import pytest

from anvil_serving import controller, controller_diagnostics, mcp
from anvil_serving.control_plane.controller import cli as controller_cli


TOKEN = "controller-secret-token"
CONTEXT = {
    "topology": "fakoli",
    "execution_host": "dark",
    "execution_runtime": "dark-native",
}


@contextlib.contextmanager
def running_controller(**kwargs):
    kwargs.setdefault("allow_unauthenticated_loopback", True)
    # Hermetic by default: a developer's real operator-home token must not
    # silently turn local unauthenticated-loopback fixtures into auth servers.
    kwargs.setdefault("env", {})
    with tempfile.TemporaryDirectory(prefix="anvil-controller-test-") as temp_dir:
        kwargs.setdefault("idempotency_db_path", os.path.join(temp_dir, "operations.sqlite3"))
        httpd = controller.make_server("127.0.0.1", 0, **kwargs)
        host, port = httpd.server_address[:2]
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield host, port
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


def _request(
    host,
    port,
    method,
    path,
    body=None,
    headers=None,
    content_type="application/json",
    mcp_defaults=True,
):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        if mcp_defaults and path == "/mcp" and isinstance(body, dict):
            body = dict(body)
            params = body.get("params")
            if params is None:
                params = {}
                body["params"] = params
            if isinstance(params, dict):
                params = dict(params)
                metadata = dict(params.get("_meta") or {})
                metadata.setdefault(
                    "io.modelcontextprotocol/protocolVersion",
                    mcp.PROTOCOL_VERSION,
                )
                metadata.setdefault(
                    "io.modelcontextprotocol/clientCapabilities",
                    {},
                )
                metadata.setdefault(
                    "io.modelcontextprotocol/clientInfo",
                    {"name": "anvil-controller-tests", "version": "1.0"},
                )
                params["_meta"] = metadata
                body["params"] = params
        payload = None if body is None else json.dumps(body)
        req_headers = {}
        if content_type is not None:
            req_headers["Content-Type"] = content_type
        if mcp_defaults and path == "/mcp" and isinstance(body, dict):
            req_headers["Accept"] = "application/json, text/event-stream"
            req_headers["MCP-Protocol-Version"] = mcp.PROTOCOL_VERSION
            request_method = body.get("method")
            if isinstance(request_method, str):
                req_headers["Mcp-Method"] = request_method
            params = body.get("params")
            if (
                request_method == "tools/call"
                and isinstance(params, dict)
                and isinstance(params.get("name"), str)
            ):
                req_headers["Mcp-Name"] = params["name"]
        if headers:
            req_headers.update(headers)
        conn.request(method, path, payload, req_headers)
        resp = conn.getresponse()
        raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, parsed, raw
    finally:
        conn.close()


def test_bind_safety_requires_auth_for_loopback_by_default():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety("127.0.0.1", env={})
    assert exc.value.code == "auth_token_required"

    assessment = controller.validate_bind_safety(
        "127.0.0.1",
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
    )
    assert assessment.loopback is True
    assert assessment.requires_auth is True

    assessment = controller.validate_bind_safety(
        "127.0.0.1",
        env={},
        allow_unauthenticated_loopback=True,
    )
    assert assessment.requires_auth is False


def test_bind_safety_requires_auth_for_tailscale_bind():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety("100.64.0.10", env={})
    assert exc.value.code == "auth_token_required"

    assessment = controller.validate_bind_safety(
        "100.64.0.10",
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
    )
    assert assessment.tailscale is True
    assert assessment.requires_auth is True


def test_controller_cli_unauthenticated_loopback_flag_is_explicit_opt_in():
    # ADR-0033: the flag exists as a documented development opt-in, default
    # off; bind safety still restricts it to strictly-loopback binds.
    parser = controller._build_parser()
    args = parser.parse_args(["serve", "--host", "127.0.0.1"])
    assert args.allow_unauthenticated_loopback is False
    args = parser.parse_args(["serve", "--allow-unauthenticated-loopback"])
    assert args.allow_unauthenticated_loopback is True


def test_controller_status_uses_bounded_authenticated_health_probe(monkeypatch, capsys):
    seen = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            assert limit == controller.DEFAULT_STATUS_MAX_RESPONSE_BYTES + 1
            return self.payload

    def open_status(request, timeout):
        seen.append(
            {
                "url": request.full_url,
                "authorization": request.get_header("Authorization"),
                "timeout": timeout,
            }
        )
        if request.full_url.endswith("/health"):
            return Response(b'{"service":"anvil-serving-controller","status":"ok"}')
        return Response(b'{"tools":[{"name":"router_status"},{"name":"host_summary"}]}')

    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", TOKEN)
    assert controller.status("http://127.0.0.1:8765", timeout=1.25, _open=open_status) == 0
    assert seen == [
        {
            "url": "http://127.0.0.1:8765/health",
            "authorization": "Bearer " + TOKEN,
            "timeout": 1.25,
        },
        {
            "url": "http://127.0.0.1:8765/tools/list",
            "authorization": "Bearer " + TOKEN,
            "timeout": 1.25,
        },
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "ok",
        "service": "anvil-serving-controller",
        "capabilities": {
            "tool_count": 2,
            "tools": ["host_summary", "router_status"],
        },
    }


def test_controller_serve_restores_python_unauthenticated_loopback_parameter():
    seen = {}

    class Server:
        server_address = ("127.0.0.1", 8765)

        def serve_forever(self):
            return None

        def server_close(self):
            return None

    def server_factory(**kwargs):
        seen.update(kwargs)
        return Server()

    assert (
        controller.serve(
            allow_unauthenticated_loopback=True,
            server_factory=server_factory,
        )
        == 0
    )
    assert seen["allow_unauthenticated_loopback"] is True


def test_controller_auth_and_health_do_not_leak_token():
    with running_controller(
        auth_token_env="ANVIL_CONTROLLER_TOKEN",
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        allow_unauthenticated_loopback=False,
    ) as (host, port):
        status, _, body, raw = _request(host, port, "GET", "/health")
        assert status == 401
        assert TOKEN not in raw.decode("utf-8")
        assert body["error"]["code"] == "authentication_error"

        status, _, body, raw = _request(
            host,
            port,
            "GET",
            "/healthz",
            headers={"Authorization": "Bearer " + TOKEN},
        )
        assert status == 200
        assert body["status"] == "ok"
        assert TOKEN not in raw.decode("utf-8")


def test_controller_mcp_2026_rejects_missing_mirrored_headers_and_old_versions():
    metadata = {
        "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {"_meta": metadata},
    }
    with running_controller() as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=request,
            mcp_defaults=False,
        )
        assert status == 400
        assert body["error"]["code"] == -32020

        old_version = "2025-11-25"
        request["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = old_version
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=request,
            headers={
                "MCP-Protocol-Version": old_version,
                "Mcp-Method": "tools/list",
            },
            mcp_defaults=False,
        )
        assert status == 400
        assert body["error"]["code"] == -32022
        assert body["error"]["data"] == {
            "requested": old_version,
            "supported": [mcp.PROTOCOL_VERSION],
        }

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=request,
            headers={
                "MCP-Protocol-Version": mcp.PROTOCOL_VERSION,
                "Mcp-Method": "tools/list",
            },
            mcp_defaults=False,
        )
        assert status == 400
        assert body["error"]["code"] == -32020


def test_controller_mcp_rejects_browser_origins_and_root_jsonrpc():
    with running_controller() as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Origin": "https://example.test"},
        )
        assert status == 403
        assert body["error"]["message"] == "Origin is not allowed by this controller"

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )
        assert status == 200
        assert body["result"]["resultType"] == "complete"

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/",
            body={"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        )
        assert status == 404
        assert body["error"]["code"] == "not_found"


def test_controller_redacts_nested_credential_shaped_result_keys():
    def fake_call_tool(name, arguments=None):
        return {
            "ok": True,
            "data": {
                "nested": {
                    "accessToken": "access-value",
                    "private.key": "private-value",
                    "client_secret": "client-value",
                    "authorization-token": "authorization-value",
                }
            },
        }

    with running_controller(call_tool_func=fake_call_tool) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {}},
        )

    assert status == 200
    assert set(body["data"]["nested"].values()) == {"<redacted>"}


def test_controller_redacts_common_cloud_credentials_from_keys_and_text():
    def fake_call_tool(name, arguments=None):
        return {
            "ok": True,
            "data": {
                "access_key": "access-value",
                "secretAccessKey": "secret-value",
                "diagnostic": "Bearer opaque-token access_key=AKIAABCDEFGHIJKLMNOP",
            },
        }

    with running_controller(call_tool_func=fake_call_tool) as (host, port):
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {}},
        )

    assert status == 200
    assert body["data"]["access_key"] == "<redacted>"
    assert body["data"]["secretAccessKey"] == "<redacted>"
    assert "opaque-token" not in raw.decode("utf-8")
    assert "AKIAABCDEFGHIJKLMNOP" not in raw.decode("utf-8")


def test_controller_new_tools_reject_token_values_and_string_booleans():
    with running_controller() as (host, port):
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "cache_prune_plan", "arguments": {"confirm": "false"}},
        )
        assert status == 200
        assert body["ok"] is False
        assert body["error"]["code"] == "bad_argument"
        assert TOKEN not in raw.decode("utf-8")

        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "cache_prune_plan", "arguments": {"api_key": TOKEN}},
        )
        assert status == 200
        assert body["ok"] is False
        assert body["error"]["code"] == "bad_argument"
        assert TOKEN not in raw.decode("utf-8")

        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "host_summary", "arguments": {"token": TOKEN}},
        )
        assert status == 200
        assert body["ok"] is False
        assert body["error"]["code"] == "bad_argument"
        assert TOKEN not in raw.decode("utf-8")


def test_controller_slow_trickle_body_hits_absolute_read_deadline():
    audits = []
    body = b'{"id":1}'
    with running_controller(audit_logger=audits.append, read_timeout_seconds=0.12) as (host, port):
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"POST /mcp HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            for byte in body:
                try:
                    sock.sendall(bytes([byte]))
                except OSError:
                    break
                time.sleep(0.05)
            raw = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                raw += chunk

    assert audits[-1]["error_code"] == "request_timeout"
    if raw:
        assert b" 408 " in raw.split(b"\r\n", 1)[0]
        assert b"request_timeout" in raw


def test_jsonrpc_notification_does_not_execute_tool_call():
    calls = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(call_tool_func=fake_call_tool) as (host, port):
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/mcp",
            body={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "fake", "arguments": {"confirm": True}},
            },
        )
    assert status == 202
    assert body is None
    assert raw == b""
    assert calls == []


def test_controller_idempotency_prevents_replay_and_survives_restart(tmp_path):
    calls = []
    db_path = str(tmp_path / "operations.sqlite3")

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {"call": len(calls)}}

    headers = {"X-Anvil-Idempotency-Key": "mutation-1", "X-Request-Id": "first"}
    with running_controller(call_tool_func=fake_call_tool, idempotency_db_path=db_path) as (
        host,
        port,
    ):
        status, _, first, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers=headers,
        )
        assert status == 200
        status, _, repeated, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers=headers,
        )
        assert status == 200
        assert repeated == first
        status, _, record, _ = _request(host, port, "GET", "/operations/mutation-1")
        assert status == 200
        assert record["status"] == "succeeded"
        assert record["request_id"] == "first"
        assert len(record["fingerprint"]) == 64
        assert record["result"]["data"]["call"] == 1

    with running_controller(call_tool_func=fake_call_tool, idempotency_db_path=db_path) as (
        host,
        port,
    ):
        status, _, repeated, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers=headers,
        )
    assert status == 200
    assert repeated == first
    assert calls == [("fake", {"confirm": True})]


def test_confirmed_mutation_without_idempotency_key_is_not_dispatched(tmp_path):
    calls = []
    with running_controller(
        call_tool_func=lambda *args: calls.append(args) or {"ok": True},
        idempotency_db_path=str(tmp_path / "operations.sqlite3"),
    ) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}},
        )

    assert status == 409
    assert body["error"]["code"] == "idempotency_key_required"
    assert calls == []


def test_controller_idempotency_rejects_mismatch_and_reports_running_failed_and_unknown(tmp_path):
    calls = []
    db_path = str(tmp_path / "operations.sqlite3")

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": False, "error": {"code": "tool_failed", "token": TOKEN}}

    running_store = controller.OperationStore(db_path)
    running_store.claim(
        "running-1",
        controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT),
        "original",
    )
    # A fresh lease marks the record as genuinely in flight; without it the
    # ADR-0033 boot reconciliation would rightly fail it as an orphan.
    running_store._write_lease("running-1")
    with running_controller(call_tool_func=fake_call_tool, operation_store=running_store) as (
        host,
        port,
    ):
        headers = {"X-Anvil-Idempotency-Key": "running-1"}
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers=headers,
        )
        assert status == 202
        assert body["error"]["code"] == "operation_running"
        status, _, record, _ = _request(host, port, "GET", "/operations/running-1")
        assert status == 200
        assert record["status"] == "running"

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": False}, "context": CONTEXT},
            headers=headers,
        )
        assert status == 409
        assert body["error"]["code"] == "idempotency_key_conflict"

        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers={"X-Anvil-Idempotency-Key": "failed-1"},
        )
        assert status == 200
        assert body["ok"] is False
        status, _, record, raw = _request(host, port, "GET", "/operations/failed-1")
        assert status == 200
        assert record["status"] == "failed"
        assert record["error"]["code"] == "tool_failed"
        assert TOKEN not in raw.decode("utf-8")
        status, _, record, _ = _request(host, port, "GET", "/operations/unknown-1")
        assert status == 200
        assert record["status"] == "unknown"
    assert calls == [("fake", {"confirm": True})]


def test_operation_status_route_decodes_percent_encoded_idempotency_key(tmp_path):
    key = "mutation:1"
    with running_controller(
        call_tool_func=lambda *args: {"ok": True},
        idempotency_db_path=str(tmp_path / "operations.sqlite3"),
    ) as (host, port):
        status, _, _, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers={"X-Anvil-Idempotency-Key": key},
        )
        assert status == 200

        status, _, record, _ = _request(host, port, "GET", "/operations/mutation%3A1")

    assert status == 200
    assert record["key"] == key
    assert record["status"] == "succeeded"


def test_controller_jsonrpc_idempotency_uses_exact_header_and_route(tmp_path):
    calls = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {"done": True}}

    with running_controller(
        list_tools_func=lambda: [
            {"name": "fake", "description": "Fake", "inputSchema": {"type": "object"}}
        ],
        call_tool_func=fake_call_tool,
        idempotency_db_path=str(tmp_path / "operations.sqlite3"),
    ) as (host, port):
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
        }
        headers = {"X-Anvil-Idempotency-Key": "jsonrpc-1"}
        status, _, first, _ = _request(
            host, port, "POST", "/mcp", body=request, headers=headers
        )
        assert status == 200
        status, _, repeated, _ = _request(
            host, port, "POST", "/mcp", body=request, headers=headers
        )
        assert status == 200
        assert repeated == first
    assert calls == [("fake", {"confirm": True})]


def test_idempotency_fingerprint_rejects_execution_context_changes(tmp_path):
    calls = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    headers = {"X-Anvil-Idempotency-Key": "context-1"}
    body = {"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT}
    with running_controller(
        call_tool_func=fake_call_tool,
        idempotency_db_path=str(tmp_path / "operations.sqlite3"),
    ) as (host, port):
        assert _request(host, port, "POST", "/tools/call", body=body, headers=headers)[0] == 200
        for field in ("topology", "execution_host", "execution_runtime"):
            changed = dict(CONTEXT)
            changed[field] += "-other"
            status, _, response, _ = _request(
                host,
                port,
                "POST",
                "/tools/call",
                body={**body, "context": changed},
                headers=headers,
            )
            assert status == 409
            assert response["error"]["code"] == "idempotency_key_conflict"
    assert len(calls) == 1


def test_idempotency_expiry_tombstones_prevent_replay_and_free_capacity(tmp_path):
    now = [100.0]
    store = controller.OperationStore(
        str(tmp_path / "operations.sqlite3"),
        retention_seconds=10,
        max_records=1,
        clock=lambda: now[0],
    )
    first_fp = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)
    assert store.claim("crashed", first_fp, "request-1")[0] == "claimed"
    assert store.claim("full", first_fp, "request-2")[0] == "full"
    now[0] = 111.0
    assert store.lookup("crashed")["status"] == "expired"
    assert store.claim("crashed", first_fp, "request-3")[0] == "expired"
    assert store.claim("crashed", "different", "request-4")[0] == "conflict"
    assert store.claim("completed", first_fp, "request-5")[0] == "claimed"
    store.complete("completed", "succeeded", {"ok": True}, None)
    now[0] = 122.0
    assert store.lookup("completed")["status"] == "expired"
    assert store.claim("completed", first_fp, "request-6")[0] == "expired"
    assert store.lookup("unknown") is None
    with running_controller(
        operation_store=store,
        call_tool_func=lambda *args: pytest.fail("expired key dispatched"),
    ) as (host, port):
        status, _, record, _ = _request(host, port, "GET", "/operations/completed")
        assert status == 200
        assert record["status"] == "expired"
        status, _, response, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers={"X-Anvil-Idempotency-Key": "completed"},
        )
        assert status == 409
        assert response["error"]["code"] == "idempotency_key_expired"


def test_idempotency_long_running_operation_completes_before_stale_compaction(tmp_path):
    now = [100.0]
    db_path = str(tmp_path / "operations.sqlite3")
    store = controller.OperationStore(
        db_path,
        retention_seconds=10,
        max_records=2,
        clock=lambda: now[0],
    )
    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)

    assert store.claim("long-running", fingerprint, "request-1")[0] == "claimed"
    with store.executing("long-running"):
        now[0] = 111.0
        assert store.lookup("long-running")["status"] == "running"
        store.complete("long-running", "succeeded", {"ok": True}, None)
    completed = store.lookup("long-running")
    assert completed["status"] == "succeeded"
    assert completed["result"] == {"ok": True}
    assert completed["expires_at"] == 121.0

    assert store.claim("crashed", fingerprint, "request-2")[0] == "claimed"
    now[0] = 122.0
    restarted = controller.OperationStore(
        db_path,
        retention_seconds=10,
        max_records=2,
        clock=lambda: now[0],
    )
    assert restarted.lookup("long-running")["status"] == "expired"
    assert restarted.lookup("crashed")["status"] == "expired"
    assert restarted.claim("crashed", fingerprint, "request-3")[0] == "expired"


def test_idempotency_execution_lease_protects_active_record_across_store_instances(tmp_path):
    db_path = str(tmp_path / "operations.sqlite3")
    owner = controller.OperationStore(db_path, retention_seconds=60, max_records=2)
    observer = controller.OperationStore(db_path, retention_seconds=60, max_records=2)
    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)
    assert owner.claim("cross-process", fingerprint, "request-1")[0] == "claimed"

    with owner.executing("cross-process"):
        with owner._connection() as connection:
            connection.execute(
                "UPDATE operation_records SET expires_at = 0 WHERE idempotency_key = ?",
                ("cross-process",),
            )
        assert observer.lookup("cross-process")["status"] == "running"
        owner.complete("cross-process", "succeeded", {"ok": True}, None)

    assert observer.lookup("cross-process")["status"] == "succeeded"


def test_idempotency_tombstone_is_purged_once_its_own_retention_elapses(tmp_path):
    """Exact-membership tombstones (no bloom filter) still expire on schedule.

    An expired record's tombstone blocks replay for one retention window, then
    is purged and the key becomes freely reusable, exercised entirely through
    the public store API (claim/lookup) plus the maintenance-path purge.
    """
    now = [100.0]
    store = controller.OperationStore(
        str(tmp_path / "operations.sqlite3"),
        retention_seconds=10,
        max_records=1,
        clock=lambda: now[0],
    )
    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)

    assert store.claim("recycled", fingerprint, "request-1")[0] == "claimed"
    now[0] = 111.0  # record itself expires; tombstoned for another retention window
    assert store.lookup("recycled")["status"] == "expired"
    assert store.claim("recycled", fingerprint, "request-2")[0] == "expired"

    now[0] = 122.0  # tombstone's own expiry (121.0) has passed; fully forgotten
    assert store.lookup("recycled") is None
    assert store.claim("recycled", fingerprint, "request-3")[0] == "claimed"


# --- ADR-0033: file-backed token resolution -------------------------------


@pytest.fixture
def token_only_in_config_env(tmp_path, monkeypatch):
    """Token absent from the shell environment, present in $ANVIL_SERVING_HOME/.env."""
    config_home = tmp_path / "anvil-home"
    user_home = tmp_path / "user-home"
    config_home.mkdir()
    user_home.mkdir()
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("ANVIL_CONTROLLER_TOKEN", raising=False)
    (config_home / ".env").write_text(
        "ANVIL_CONTROLLER_TOKEN=%s\n" % TOKEN, encoding="utf-8"
    )
    return config_home


def test_resolve_auth_token_falls_back_to_operator_dotenv(token_only_in_config_env):
    assert controller.resolve_auth_token(required=True) == TOKEN


@pytest.mark.parametrize("disable_home_environment, expected_source_count", [("0", 3), ("1", 2)])
def test_resolve_auth_token_missing_names_sources(
    tmp_path, monkeypatch, disable_home_environment, expected_source_count
):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-user"))
    monkeypatch.delenv("ANVIL_CONTROLLER_TOKEN", raising=False)
    monkeypatch.setenv("ANVIL_SERVING_NO_HOME_ENV", disable_home_environment)
    with pytest.raises(controller.ControllerError) as exc:
        controller.resolve_auth_token(required=True)
    assert exc.value.code == "auth_token_missing"
    sources = exc.value.details["sources_checked"]
    assert sources[0] == "environment variable ANVIL_CONTROLLER_TOKEN"
    assert len(sources) == expected_source_count


# --- ADR-0033: boot reconciliation of orphaned operations ------------------


def test_controller_orphaned_running_operation_fails_closed_after_restart(tmp_path):
    calls = []
    db_path = str(tmp_path / "operations.sqlite3")

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)
    seeded = controller.OperationStore(db_path)
    assert seeded.claim("interrupted-1", fingerprint, "original") == ("claimed", None)

    audit_records = []
    with running_controller(
        call_tool_func=fake_call_tool,
        idempotency_db_path=db_path,
        audit_logger=audit_records.append,
    ) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers={"X-Anvil-Idempotency-Key": "interrupted-1", "X-Request-Id": "retry"},
        )
        assert status == 200
        assert body["ok"] is False
        assert body["error"]["code"] == "operation_interrupted"
        status, _, record, _ = _request(host, port, "GET", "/operations/interrupted-1")
        assert status == 200
        assert record["status"] == "failed"

        # A mismatched fingerprint replay still conflicts after recovery.
        status, _, conflict, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True, "x": 1}, "context": CONTEXT},
            headers={"X-Anvil-Idempotency-Key": "interrupted-1", "X-Request-Id": "later"},
        )
        assert status == 409

    assert calls == []
    events = [r for r in audit_records if r.get("event") == "operation_interrupted_recovered"]
    assert [e["key"] for e in events] == ["interrupted-1"]
    assert events[0]["request_id"] == "original"


# --- ADR-0033: durable JSONL audit sink ------------------------------------


def test_controller_audit_log_file_sink(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    with running_controller(audit_log_path=str(audit_path)) as (host, port):
        status, _, _, _ = _request(host, port, "GET", "/health")
        assert status == 200
    raw = audit_path.read_text(encoding="utf-8")
    lines = [line for line in raw.strip().splitlines() if line]
    assert lines, "audit file must contain at least one record"
    record = json.loads(lines[-1])
    assert record["operation"] == "health"
    assert TOKEN not in raw


def _scoped_tools():
    return [
        {"name": "workloads.read", "_meta": {"anvil/requiredScope": "workloads:read"}},
        {"name": "nodes.bootstrap", "_meta": {"anvil/requiredScope": "node-admin:bootstrap"}},
        {"name": "legacy.operation", "_meta": {"anvil/requiredScope": None}},
    ]


def _authorization_policy(tmp_path, clients):
    path = tmp_path / "authorization-policy.json"
    path.write_text(json.dumps({"schema_version": 1, "clients": clients}), encoding="utf-8")
    return str(path)


_DIAGNOSTIC_CONTAINER_ID = "a" * 64


class _DiagnosticCaptureSpy:
    def __init__(self, *stdout_values):
        self._stdout_values = list(stdout_values)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), dict(kwargs)))
        return controller_diagnostics.ChildCapture(
            "ok",
            self._stdout_values.pop(0),
            b"",
            False,
        )


def _install_diagnostic_capture(monkeypatch, capture):
    inspect_controller = controller_diagnostics.inspect_controller
    controller_logs = controller_diagnostics.controller_logs

    def inspect_with_capture(container, **kwargs):
        kwargs["platform"] = "linux"
        kwargs["_capture"] = capture
        return inspect_controller(container, **kwargs)

    monkeypatch.setattr(
        controller_diagnostics,
        "inspect_controller",
        inspect_with_capture,
    )
    monkeypatch.setattr(
        controller_diagnostics,
        "controller_logs",
        lambda container, tail: controller_logs(
            container,
            tail,
            platform="linux",
            _capture=capture,
        ),
    )


def _call_diagnostic(host, port, name, token=None):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    arguments = {"container": "anvil-serving-controller"}
    if name == "controller_logs":
        arguments["tail"] = 17
    return _request(
        host,
        port,
        "POST",
        "/tools/call",
        {"name": name, "arguments": arguments},
        headers,
    )


def test_controller_diagnostic_authorization_denials_start_no_child(tmp_path, monkeypatch):
    workload_token = "scoped-workload-token"
    bootstrap_token = "scoped-bootstrap-token"
    capture = _DiagnosticCaptureSpy()
    _install_diagnostic_capture(monkeypatch, capture)
    policy = _authorization_policy(
        tmp_path,
        [
            {"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"},
            {
                "id": "bootstrap",
                "scopes": ["node-admin:bootstrap"],
                "credential_env": "BOOTSTRAP",
            },
        ],
    )
    tools = ("controller_inspect", "controller_logs")
    with running_controller(
        env={
            "ANVIL_CONTROLLER_TOKEN": TOKEN,
            "WORKLOAD": workload_token,
            "BOOTSTRAP": bootstrap_token,
        },
        authorization_policy=policy,
        allowed_operations=tools,
    ) as (host, port):
        for name in tools:
            for token in (None, "wrong-controller-token"):
                status, _, body, _ = _call_diagnostic(host, port, name, token)
                assert status == 401
                assert body["error"]["code"] == "authentication_error"
            for token in (workload_token, bootstrap_token):
                status, _, body, _ = _call_diagnostic(host, port, name, token)
                assert status == 403
                assert body["error"]["code"] == "authorization_scope_denied"

    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        allowed_operations=("router_status",),
    ) as (host, port):
        for name in tools:
            status, _, body, _ = _call_diagnostic(host, port, name, TOKEN)
            assert status == 400
            assert body["error"]["code"] == "unknown_tool"

    assert capture.calls == []


def test_scoped_controller_policy_keeps_legacy_and_new_operations_separate(tmp_path):
    workload_token = "scoped-workload-token"
    bootstrap_token = "scoped-bootstrap-token"
    policy = _authorization_policy(
        tmp_path,
        [
            {"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"},
            {"id": "bootstrap", "scopes": ["node-admin:bootstrap"], "credential_env": "BOOTSTRAP"},
        ],
    )
    calls = []

    def call_tool(name, arguments=None):
        calls.append(name)
        return {"ok": True, "name": name}

    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": workload_token, "BOOTSTRAP": bootstrap_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=call_tool,
    ) as (host, port):
        scoped_headers = {"Authorization": "Bearer " + workload_token}
        legacy_headers = {"Authorization": "Bearer " + TOKEN}
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "workloads.read"}, scoped_headers)
        assert status == 200 and body["ok"] is True
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "nodes.bootstrap"}, scoped_headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "legacy.operation"}, scoped_headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "workloads.read"}, legacy_headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "legacy.operation"}, legacy_headers)
        assert status == 200 and body["ok"] is True
    assert calls == ["workloads.read", "legacy.operation"]


def test_propagation_status_scope_is_authenticated_but_cannot_gain_other_operations(tmp_path):
    status_token = "scoped-propagation-status-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "status", "scopes": ["propagation:status"], "credential_env": "STATUS"}],
    )
    tools = [
        {"name": "propagation_capabilities", "_meta": {"anvil/requiredScope": "propagation:status"}},
        {"name": "propagation_admit", "_meta": {"anvil/requiredScope": "propagation:admission"}},
        {"name": "legacy.operation", "_meta": {"anvil/requiredScope": None}},
    ]
    calls = []
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "STATUS": status_token}, authorization_policy=policy,
        list_tools_func=lambda: tools, call_tool_func=lambda name, arguments=None: calls.append(name) or {"ok": True},
    ) as (host, port):
        headers = {"Authorization": "Bearer " + status_token}
        status, _, body, _ = _request(host, port, "GET", "/tools/list", headers=headers)
        assert status == 200 and [tool["name"] for tool in body["tools"]] == ["propagation_capabilities"]
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_admit"}, headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "legacy.operation"}, headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
    assert calls == []


def test_unauthenticated_loopback_does_not_bypass_protected_propagation_scope():
    calls = []
    with running_controller(
        list_tools_func=lambda: [
            {"name": "propagation_capabilities", "_meta": {"anvil/requiredScope": "propagation:status"}},
        ],
        call_tool_func=lambda *args: calls.append(args) or {"ok": True},
    ) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list")
        assert status == 200 and body["tools"] == []
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_capabilities"})
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
    assert calls == []


def test_default_mcp_catalog_keeps_propagation_internal_and_scopes_real_dispatch(tmp_path):
    token = "scoped-propagation-status-token"
    policy = _authorization_policy(tmp_path, [{"id": "status", "scopes": ["propagation:status"], "credential_env": "STATUS"}])
    with running_controller(env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "STATUS": token}, authorization_policy=policy) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list", headers={"Authorization": "Bearer " + token})
        assert status == 200 and "propagation_capabilities" in [tool["name"] for tool in body["tools"]]
        status, _, body, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, {"Authorization": "Bearer " + token})
        assert status == 200 and "propagation_capabilities" in [tool["name"] for tool in body["result"]["tools"]]
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_capabilities"}, {"Authorization": "Bearer " + token})
        assert status == 200 and body["ok"] is True
        status, _, body, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "propagation_capabilities", "arguments": {}}}, {"Authorization": "Bearer " + token})
        assert status == 200 and body["result"]["structuredContent"]["ok"] is True
        status, _, body, _ = _request(host, port, "GET", "/tools/list", headers={"Authorization": "Bearer " + TOKEN})
        assert status == 200 and "propagation_capabilities" not in [tool["name"] for tool in body["tools"]]
        status, _, body, _ = _request(host, port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "propagation_capabilities", "arguments": {}}}, {"Authorization": "Bearer " + TOKEN})
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_capabilities"}, {"Authorization": "Bearer " + TOKEN})
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "GET", "/tools/list")
        assert status == 401 and body["error"]["code"] == "authentication_error"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "propagation_capabilities"})
        assert status == 401 and body["error"]["code"] == "authentication_error"


def test_scoped_discovery_and_operations_are_principal_filtered(tmp_path):
    scoped_token = "scoped-workload-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"}],
    )

    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": scoped_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
    ) as (host, port):
        scoped_headers = {"Authorization": "Bearer " + scoped_token}
        legacy_headers = {"Authorization": "Bearer " + TOKEN}
        status, _, body, _ = _request(host, port, "GET", "/tools/list", headers=scoped_headers)
        assert status == 200
        assert [tool["name"] for tool in body["tools"]] == ["workloads.read"]
        status, _, body, _ = _request(host, port, "GET", "/tools/list", headers=legacy_headers)
        assert status == 200
        assert [tool["name"] for tool in body["tools"]] == ["legacy.operation"]
        status, _, body, _ = _request(host, port, "GET", "/operations/not-a-real-key", headers=scoped_headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"


def test_scoped_policy_failure_disables_only_new_scopes(tmp_path):
    policy = tmp_path / "authorization-policy.json"
    policy.write_text("{malformed", encoding="utf-8")
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": "scoped-workload-token"},
        authorization_policy=str(policy),
        list_tools_func=_scoped_tools,
        call_tool_func=lambda name, arguments=None: {"ok": True},
    ) as (host, port):
        status, _, body, _ = _request(
            host, port, "POST", "/tools/call", {"name": "workloads.read"}, {"Authorization": "Bearer " + TOKEN}
        )
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(
            host, port, "POST", "/tools/call", {"name": "legacy.operation"}, {"Authorization": "Bearer " + TOKEN}
        )
        assert status == 200 and body["ok"] is True


def test_scoped_credential_is_redacted_from_response_and_persisted_result(tmp_path):
    scoped_token = "scoped-workload-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"}],
    )
    audit = []
    store = controller.OperationStore(str(tmp_path / "operations.sqlite3"))

    def call_tool(name, arguments=None):
        return {"ok": True, "echo": scoped_token}

    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": scoped_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=call_tool,
        audit_logger=audit.append,
        operation_store=store,
    ) as (host, port):
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            {"name": "workloads.read"},
            {"Authorization": "Bearer " + scoped_token, "Idempotency-Key": "scoped-redaction"},
        )
        assert status == 200 and body["ok"] is True
        assert scoped_token.encode() not in raw
    assert scoped_token not in json.dumps(store.lookup("scoped-redaction"))
    assert scoped_token not in json.dumps(audit)


def test_controller_cli_forwards_authorization_policy(monkeypatch):
    seen = {}

    def fake_serve(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("anvil_serving.control_plane.controller.cli.serve", fake_serve)
    assert controller_cli.main(["serve", "--authorization-policy", "policy.json"]) == 0
    assert seen["authorization_policy"] == "policy.json"


@pytest.mark.parametrize("content_length", [17, 2 * 1024 * 1024])
def test_scoped_mcp_wrong_scope_closes_before_unread_body(tmp_path, content_length):
    scoped_token = "scoped-workload-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"}],
    )
    calls = []
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": scoped_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=lambda *args: calls.append(args) or {"ok": True},
    ) as (host, port):
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                (
                    "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    "Authorization: Bearer %s\r\nContent-Type: application/json\r\n"
                    "Content-Length: %s\r\nMcp-Method: tools/call\r\n"
                    "Mcp-Name: nodes.bootstrap\r\nConnection: keep-alive\r\n\r\n"
                ).encode() % (scoped_token.encode(), str(content_length).encode())
            )
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks)
            assert b" 403 " in response
            assert b"Connection: close" in response
    assert calls == []


def test_legacy_new_scope_and_scoped_operation_status_skip_store_and_handler(tmp_path):
    class SpyStore:
        def __init__(self):
            self.claims = 0
            self.lookups = 0

        def recover_interrupted(self):
            return []

        def claim(self, *args):
            self.claims += 1
            raise AssertionError("scope check must precede store claim")

        def lookup(self, *args):
            self.lookups += 1
            raise AssertionError("scope check must precede store lookup")

    scoped_token = "scoped-workload-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"}],
    )
    store = SpyStore()
    calls = []
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": scoped_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=lambda *args: calls.append(args) or {"ok": True},
        operation_store=store,
    ) as (host, port):
        status, _, body, _ = _request(
            host, port, "POST", "/tools/call", {"name": "workloads.read"}, {"Authorization": "Bearer " + TOKEN}
        )
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(
            host, port, "GET", "/operations/blocked", headers={"Authorization": "Bearer " + scoped_token}
        )
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
    assert store.claims == store.lookups == 0
    assert calls == []


def test_keepalive_resets_scoped_principal_between_requests(tmp_path):
    scoped_token = "scoped-workload-token"
    policy = _authorization_policy(
        tmp_path,
        [{"id": "workload", "scopes": ["workloads:read"], "credential_env": "WORKLOAD"}],
    )
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, "WORKLOAD": scoped_token},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=lambda *args: {"ok": True},
    ) as (host, port):
        conn = http.client.HTTPConnection(host, port, timeout=2)
        try:
            conn.request("POST", "/tools/call", json.dumps({"name": "workloads.read"}), {
                "Content-Type": "application/json", "Authorization": "Bearer " + scoped_token,
            })
            first = conn.getresponse()
            assert first.status == 200
            first.read()
            conn.request("POST", "/tools/call", json.dumps({"name": "workloads.read"}), {
                "Content-Type": "application/json", "Authorization": "Bearer " + TOKEN,
            })
            response = conn.getresponse()
            assert response.status == 403
            assert json.loads(response.read())["error"]["code"] == "authorization_scope_denied"
        finally:
            conn.close()


@pytest.mark.parametrize(
    "clients, environment",
    [
        (
            [
                {"id": "first", "scopes": ["workloads:read"], "credential_env": "DUP"},
                {"id": "second", "scopes": ["node-admin:bootstrap"], "credential_env": "DUP"},
            ],
            {"DUP": "scoped-workload-token"},
        ),
        (
            [{"id": "legacy", "scopes": ["workloads:read"], "credential_env": "LEGACY"}],
            {"LEGACY": TOKEN},
        ),
    ],
)
def test_invalid_scoped_policy_never_grants_new_scope_but_legacy_remains(tmp_path, clients, environment):
    policy = _authorization_policy(tmp_path, clients)
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN, **environment},
        authorization_policy=policy,
        list_tools_func=_scoped_tools,
        call_tool_func=lambda *args: {"ok": True},
    ) as (host, port):
        headers = {"Authorization": "Bearer " + TOKEN}
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "workloads.read"}, headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "legacy.operation"}, headers)
        assert status == 200 and body["ok"] is True


def test_malformed_catalog_scope_fails_closed_without_disabling_legacy_tool():
    tools = _scoped_tools() + [{"name": "malformed.scope", "_meta": {"anvil/requiredScope": {}}}]
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        list_tools_func=lambda: tools,
        call_tool_func=lambda *args: {"ok": True},
    ) as (host, port):
        headers = {"Authorization": "Bearer " + TOKEN}
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "malformed.scope"}, headers)
        assert status == 403 and body["error"]["code"] == "authorization_scope_denied"
        status, _, body, _ = _request(host, port, "POST", "/tools/call", {"name": "legacy.operation"}, headers)
        assert status == 200 and body["ok"] is True
