"""Tests for the ADR-0014 controller transport.

Hermetic: live HTTP tests bind only to 127.0.0.1:0 with fake MCP functions.
"""
import contextlib
import http.client
import json
import threading

import pytest

from anvil_serving import cli, controller


TOKEN = "controller-secret-token"


@contextlib.contextmanager
def running_controller(**kwargs):
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


def _request(host, port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        payload = None if body is None else json.dumps(body)
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)
        conn.request(method, path, payload, req_headers)
        resp = conn.getresponse()
        raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, parsed, raw
    finally:
        conn.close()


def test_bind_safety_allows_loopback_without_auth():
    assessment = controller.validate_bind_safety("127.0.0.1", env={})
    assert assessment.loopback is True
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


def test_controller_auth_and_health_do_not_leak_token():
    with running_controller(auth_token_env="ANVIL_CONTROLLER_TOKEN",
                            env={"ANVIL_CONTROLLER_TOKEN": TOKEN}) as (host, port):
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


def test_controller_cli_dispatch(monkeypatch):
    from anvil_serving import controller as controller_mod

    seen = {}

    def fake_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(controller_mod, "main", fake_main)
    assert cli.main(["controller", "serve", "--host", "127.0.0.1"]) == 0
    assert seen["argv"] == ["serve", "--host", "127.0.0.1"]
