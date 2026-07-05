"""Tests for the ADR-0014 controller transport.

Hermetic: live HTTP tests bind only to 127.0.0.1:0 with fake MCP functions.
"""
import contextlib
import http.client
import json
import socket
import threading
import time

import pytest

from anvil_serving import cli, controller


TOKEN = "controller-secret-token"


@contextlib.contextmanager
def running_controller(**kwargs):
    kwargs.setdefault("allow_unauthenticated_loopback", True)
    httpd = controller.make_server("127.0.0.1", 0, **kwargs)
    host, port = httpd.server_address[:2]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield host, port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(host, port, method, path, body=None, headers=None, content_type="application/json"):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        payload = None if body is None else json.dumps(body)
        req_headers = {}
        if content_type is not None:
            req_headers["Content-Type"] = content_type
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


def test_bind_safety_rejects_localhost():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety("localhost", env={})
    assert exc.value.code == "localhost_not_allowed"


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


def test_bind_safety_refuses_public_bind_without_hard_gate():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety(
            "8.8.8.8",
            env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        )
    assert exc.value.code == "public_bind_refused"

    assessment = controller.validate_bind_safety(
        "8.8.8.8",
        allow_public_bind=True,
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
    )
    assert assessment.public is True
    assert assessment.requires_auth is True


def test_bind_safety_refuses_numeric_wildcard_alias_without_hard_gate():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety(
            "0",
            env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        )
    assert exc.value.code == "public_bind_refused"


@pytest.mark.parametrize("host", ["169.254.169.254", "192.0.2.1", "198.51.100.1", "203.0.113.1"])
def test_bind_safety_rejects_linklocal_and_documentation_ranges(host):
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety(
            host,
            allow_public_bind=True,
            env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        )
    assert exc.value.code == "unsafe_bind_address"


def test_make_server_rejects_loopback_without_token_by_default():
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.make_server("127.0.0.1", 0, env={})
    assert exc.value.code == "auth_token_required"


def test_controller_auth_and_health_do_not_leak_token():
    with running_controller(auth_token_env="ANVIL_CONTROLLER_TOKEN",
                            env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
                            allow_unauthenticated_loopback=False) as (host, port):
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


def test_controller_lists_and_calls_tools_over_jsonrpc_and_rest():
    calls = []
    audits = []

    def fake_list_tools():
        return [{"name": "fake", "description": "Fake", "inputSchema": {"type": "object"}}]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {
            "ok": True,
            "data": {
                "name": name,
                "arguments": arguments or {},
                "diagnostic": TOKEN,
            },
        }

    with running_controller(
        auth_token_env="ANVIL_CONTROLLER_TOKEN",
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        list_tools_func=fake_list_tools,
        call_tool_func=fake_call_tool,
        audit_logger=audits.append,
    ) as (host, port):
        auth = {"x-api-key": TOKEN, "X-Request-Id": "req-1"}
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/",
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=auth,
        )
        assert status == 200
        assert body["result"]["tools"][0]["name"] == "fake"
        assert TOKEN not in raw.decode("utf-8")

        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "fake",
                    "arguments": {"dry_run": True, "confirm": False},
                },
            },
            headers=auth,
        )
        assert status == 200
        envelope = body["result"]["structuredContent"]
        assert envelope["ok"] is True
        assert envelope["data"]["diagnostic"] == "<redacted>"
        assert calls[-1] == ("fake", {"dry_run": True, "confirm": False})
        assert TOKEN not in raw.decode("utf-8")

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": {"confirm": True}},
            headers=auth,
        )
        assert status == 200
        assert body["ok"] is True
        assert body["request_id"] == "req-1"

    assert any(a["operation"] == "root" and a["tool"] == "fake" for a in audits)
    assert any(a["operation"] == "tools/call" and a["confirm"] is True for a in audits)
    assert TOKEN not in json.dumps(audits)


def test_controller_bad_tool_call_is_structured_and_audited():
    audits = []
    with running_controller(audit_logger=audits.append) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "fake", "arguments": "not-an-object"},
        )
    assert status == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    assert audits[-1]["error_code"] == "bad_request"


def test_text_plain_loopback_post_cannot_execute_even_in_unsafe_dev_mode():
    calls = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(call_tool_func=fake_call_tool) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "fake", "arguments": {"confirm": True}},
            },
            content_type="text/plain",
        )
    assert status == 415
    assert body["error"]["code"] == "unsupported_media_type"
    assert calls == []


def test_controller_rejects_duplicate_content_length():
    audits = []
    with running_controller(audit_logger=audits.append) as (host, port):
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"POST / HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 8\r\n"
                b"Content-Length: 8\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"{\"id\":1}"
            )
            raw = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                raw += chunk

    assert audits[-1]["error_code"] == "bad_content_length"
    assert b" 400 " in raw.split(b"\r\n", 1)[0]


def test_controller_partial_body_read_times_out_and_is_audited():
    audits = []
    with running_controller(audit_logger=audits.append, read_timeout_seconds=0.1) as (host, port):
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"POST / HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 128\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"{\"jsonrpc\":\"2.0\""
            )
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


def test_controller_slow_trickle_body_hits_absolute_read_deadline():
    audits = []
    body = b'{"id":1}'
    with running_controller(audit_logger=audits.append, read_timeout_seconds=0.12) as (host, port):
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"POST / HTTP/1.1\r\n"
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
            "/",
            body={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "fake", "arguments": {"confirm": True}},
            },
        )
    assert status == 204
    assert body is None
    assert raw == b""
    assert calls == []


def test_jsonrpc_id_null_does_not_execute_tool_call():
    calls = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(call_tool_func=fake_call_tool) as (host, port):
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": None,
                "method": "tools/call",
                "params": {"name": "fake", "arguments": {"confirm": True}},
            },
        )
    assert status == 200
    assert body["error"]["code"] == -32600
    assert raw
    assert calls == []


def test_jsonrpc_unknown_tool_is_protocol_error_and_audited():
    calls = []
    audits = []

    def fake_list_tools():
        return [{"name": "known", "description": "Known", "inputSchema": {"type": "object"}}]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(
        list_tools_func=fake_list_tools,
        call_tool_func=fake_call_tool,
        audit_logger=audits.append,
    ) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "not_a_tool", "arguments": {"confirm": True}},
            },
        )
    assert status == 200
    assert body["error"]["code"] == -32602
    assert body["error"]["data"]["code"] == "unknown_tool"
    assert calls == []
    assert audits[-1]["ok"] is False
    assert audits[-1]["error_code"] == "unknown_tool"


def test_jsonrpc_falsey_non_object_arguments_are_rejected_and_not_called():
    calls = []

    def fake_list_tools():
        return [{"name": "known", "description": "Known", "inputSchema": {"type": "object"}}]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(
        list_tools_func=fake_list_tools,
        call_tool_func=fake_call_tool,
    ) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "known", "arguments": False},
            },
        )
    assert status == 200
    assert body["error"]["code"] == -32602
    assert body["error"]["data"]["code"] == "bad_arguments"
    assert calls == []


def test_jsonrpc_falsey_non_object_params_are_rejected_and_not_called():
    calls = []

    def fake_list_tools():
        return [{"name": "known", "description": "Known", "inputSchema": {"type": "object"}}]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with running_controller(
        list_tools_func=fake_list_tools,
        call_tool_func=fake_call_tool,
    ) as (host, port):
        for value in (False, 0, "", []):
            status, _, body, _ = _request(
                host,
                port,
                "POST",
                "/",
                body={
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": value,
                },
            )
            assert status == 200
            assert body["error"]["code"] == -32602
            assert body["error"]["message"] == "params must be an object"
    assert calls == []


def test_controller_cli_dispatch(monkeypatch):
    from anvil_serving import controller as controller_mod

    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(controller_mod, "main", fake_main)
    assert cli.main(["controller", "serve", "--host", "127.0.0.1"]) == 0
    assert seen["argv"] == ["serve", "--host", "127.0.0.1"]
