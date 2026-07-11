"""Tests for the ADR-0014 controller transport.

Hermetic: live HTTP tests bind only to 127.0.0.1:0 with fake MCP functions.
"""

import contextlib
import http.client
import io
import json
import socket
import threading
import time

import pytest

from anvil_serving import cli, controller, mcp


TOKEN = "controller-secret-token"
CONTEXT = {
    "topology": "fakoli",
    "execution_host": "dark",
    "execution_runtime": "dark-native",
}


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


def test_controller_cli_rejects_unauthenticated_loopback_flag():
    with pytest.raises(SystemExit) as exc:
        controller.main(["serve", "--allow-unauthenticated-loopback"])
    assert exc.value.code == 2


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


def test_controller_status_requires_token_before_network(capsys):
    def fail_open(*_args, **_kwargs):
        pytest.fail("status attempted network access without a token")

    assert controller.status(environment={}, _open=fail_open) == 3
    assert "ANVIL_CONTROLLER_TOKEN" in capsys.readouterr().err


def test_controller_status_rejects_missing_capability(monkeypatch, capsys):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.payload

    def open_status(request, timeout):
        assert timeout == 5.0
        if request.full_url.endswith("/health"):
            return Response(b'{"service":"anvil-serving-controller","status":"ok"}')
        return Response(b'{"tools":[{"name":"host_summary"}]}')

    monkeypatch.setenv("ANVIL_CONTROLLER_TOKEN", TOKEN)
    assert (
        controller.status(
            required_operations=("router-status",),
            _open=open_status,
        )
        == 1
    )
    assert "router_status" in capsys.readouterr().err


def test_controller_allowlist_filters_catalog_and_dispatch():
    calls = []

    def fake_list_tools():
        return [
            {"name": "router_status", "inputSchema": {"type": "object"}},
            {"name": "host_summary", "inputSchema": {"type": "object"}},
        ]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {}}

    with running_controller(
        list_tools_func=fake_list_tools,
        call_tool_func=fake_call_tool,
        allowed_operations=("host-summary",),
    ) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list")
        assert status == 200
        assert [tool["name"] for tool in body["tools"]] == ["host_summary"]

        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "router_status", "arguments": {}},
        )
        assert status == 400
        assert body["error"]["code"] == "unknown_tool"
    assert calls == []


def test_controller_allowlist_maps_canonical_commands_to_shared_tools():
    tools = [
        {"name": "router_manage", "inputSchema": {"type": "object"}},
        {"name": "host_summary", "inputSchema": {"type": "object"}},
    ]

    with running_controller(
        list_tools_func=lambda: tools,
        allowed_operations=("router-up", "controller-status"),
    ) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/tools/list")

    assert status == 200
    assert [tool["name"] for tool in body["tools"]] == ["router_manage"]


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
            body={"name": "fake", "arguments": {"confirm": True}, "context": CONTEXT},
            headers={**auth, "X-Anvil-Idempotency-Key": "fake-confirmed"},
        )
        assert status == 200
        assert body["ok"] is True
        assert body["request_id"] == "req-1"

    assert any(a["operation"] == "root" and a["tool"] == "fake" for a in audits)
    assert any(a["operation"] == "tools/call" and a["confirm"] is True for a in audits)
    assert TOKEN not in json.dumps(audits)


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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_controller_json_serializer_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError):
        controller._json_dumps({"value": value})


def test_controller_normalizes_hyphenated_operations_through_mcp_registry_seam():
    calls = []

    def fake_list_tools():
        return [
            {
                "name": "router_status",
                "description": "Router status",
                "inputSchema": {"type": "object"},
            }
        ]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {"name": name}}

    with running_controller(list_tools_func=fake_list_tools, call_tool_func=fake_call_tool) as (
        host,
        port,
    ):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "router-status", "arguments": {"detail": "short"}},
        )

    assert status == 200
    assert body["ok"] is True
    assert calls == [("router_status", {"detail": "short"})]


def test_controller_dispatches_hyphenated_canonical_catalog_name():
    calls = []

    def fake_list_tools():
        return [
            {
                "name": "router-status",
                "description": "Router status",
                "inputSchema": {"type": "object"},
            }
        ]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {"name": name}}

    with running_controller(list_tools_func=fake_list_tools, call_tool_func=fake_call_tool) as (
        host,
        port,
    ):
        status, _, listed, _ = _request(host, port, "GET", "/tools/list")
        assert status == 200
        assert listed["tools"][0]["name"] == "router-status"

        status, _, rest_body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={"name": "router_status", "arguments": {"source": "rest"}},
        )
        assert status == 200
        assert rest_body["data"]["name"] == "router-status"

        status, _, rpc_body, _ = _request(
            host,
            port,
            "POST",
            "/",
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "router-status", "arguments": {"source": "jsonrpc"}},
            },
        )
        assert status == 200
        assert rpc_body["result"]["structuredContent"]["data"]["name"] == "router-status"

    assert calls == [
        ("router-status", {"source": "rest"}),
        ("router-status", {"source": "jsonrpc"}),
    ]


def test_controller_rejects_hyphen_underscore_tool_catalog_collision_before_dispatch():
    calls = []

    def fake_list_tools():
        return [
            {"name": "router-status", "description": "Hyphen", "inputSchema": {"type": "object"}},
            {
                "name": "router_status",
                "description": "Underscore",
                "inputSchema": {"type": "object"},
            },
        ]

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True}

    with pytest.raises(controller.ControllerError) as exc:
        controller.make_handler(list_tools_func=fake_list_tools, call_tool_func=fake_call_tool)

    assert exc.value.code == "ambiguous_tool_catalog"
    assert exc.value.status == 500
    assert exc.value.details == {"tools": ["router-status", "router_status"]}
    assert calls == []


def test_controller_tools_list_matches_mcp_for_host_cache_tools():
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert mcp.main(["--list-tools"]) == 0
    same_host_tools = json.loads(stdout.getvalue())["tools"]

    with running_controller() as (host, port):
        status, _, body, raw = _request(host, port, "GET", "/tools/list")

    assert status == 200
    assert body["tools"] == same_host_tools
    controller_tools = {
        tool["name"]: tool
        for tool in body["tools"]
        if tool["name"] in {"host_summary", "cache_prune_plan"}
    }
    assert controller_tools["host_summary"]["inputSchema"]["properties"] == {}
    assert "execute" not in controller_tools["cache_prune_plan"]["inputSchema"]["properties"]
    assert TOKEN not in raw.decode("utf-8")


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

        token_like = "TOKEN_123"
        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={
                "name": "route_decision",
                "arguments": {
                    "prompt": "hello",
                    "api_key_env": token_like,
                },
            },
        )
        assert status == 200
        assert body["ok"] is False
        assert body["error"]["code"] == "unsafe_api_key_env"
        assert token_like not in raw.decode("utf-8")


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
                b'{"id":1}'
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
                b'{"jsonrpc":"2.0"'
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


def test_oversized_persisted_result_replays_as_typed_failure(tmp_path):
    db_path = str(tmp_path / "operations.sqlite3")
    store = controller.OperationStore(db_path, max_result_bytes=32)
    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)
    assert store.claim("oversized-1", fingerprint, "original")[0] == "claimed"
    store.complete(
        "oversized-1",
        "succeeded",
        {"ok": True, "data": {"payload": "x" * 128}},
        None,
    )

    with running_controller(
        operation_store=store,
        call_tool_func=lambda *args: pytest.fail("oversized result replayed mutation"),
    ) as (host, port):
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/tools/call",
            body={
                "name": "fake",
                "arguments": {"confirm": True},
                "context": CONTEXT,
            },
            headers={"X-Anvil-Idempotency-Key": "oversized-1"},
        )

    assert status == 200
    assert body["ok"] is False
    assert body["error"]["code"] == "persisted_result_too_large"
    record = store.lookup("oversized-1")
    assert record["status"] == "failed"
    assert record["error"]["code"] == "persisted_result_too_large"


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


@pytest.mark.parametrize("path_segment", ["bad%", "bad%GG", "%FF", "bad%2Fkey", "bad%20key"])
def test_operation_status_route_rejects_malformed_encoded_or_invalid_keys(path_segment):
    audits = []
    with running_controller(audit_logger=audits.append) as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/operations/" + path_segment)

    assert status == 400
    assert body["error"]["code"] == "bad_idempotency_key"
    assert audits[-1]["error_code"] == "bad_idempotency_key"


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
        status, _, first, _ = _request(host, port, "POST", "/", body=request, headers=headers)
        assert status == 200
        status, _, repeated, _ = _request(host, port, "POST", "/", body=request, headers=headers)
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


def test_idempotency_expiry_tombstones_prevent_replay_and_free_capacity(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(controller.time, "time", lambda: now[0])
    store = controller.OperationStore(
        str(tmp_path / "operations.sqlite3"), retention_seconds=10, max_records=1
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


def test_idempotency_long_running_operation_completes_before_stale_compaction(
    tmp_path, monkeypatch
):
    now = [100.0]
    monkeypatch.setattr(controller.time, "time", lambda: now[0])
    db_path = str(tmp_path / "operations.sqlite3")
    store = controller.OperationStore(db_path, retention_seconds=10, max_records=2)
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
    restarted = controller.OperationStore(db_path, retention_seconds=10, max_records=2)
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


def test_persisted_sanitizer_handles_tuples_and_token_bearing_dict_keys():
    secret = "known-controller-token"
    safe = controller._sanitize_persisted_value(
        {f"prefix-{secret}-suffix": (secret, {secret: "value"})},
        secret,
    )

    assert secret not in json.dumps(safe)


def test_idempotency_tombstone_generations_rotate_saturated_false_positives(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(controller.time, "time", lambda: now[0])
    store = controller.OperationStore(
        str(tmp_path / "operations.sqlite3"), retention_seconds=10, max_records=1
    )
    fingerprint = controller._operation_fingerprint("fake", {"confirm": True}, CONTEXT)

    assert store.lookup("initialize-generation") is None
    saturated = bytes([0xFF]) * store._tombstone_bytes
    empty = bytes(store._tombstone_bytes)
    with store._connection() as connection:
        connection.execute(
            """
            UPDATE operation_tombstones
            SET key_bits = ?, fingerprint_bits = ?
            WHERE singleton = 1
            """,
            (saturated, empty),
        )

    assert store.claim("false-positive", fingerprint, "request-1")[0] == "conflict"
    now[0] = 110.0
    assert store.claim("previous-generation", fingerprint, "request-2")[0] == "conflict"
    now[0] = 120.0
    assert store.claim("rotation-recovered", fingerprint, "request-3")[0] == "claimed"

    with store._connection() as connection:
        row = connection.execute(
            "SELECT * FROM operation_tombstones WHERE singleton = 1"
        ).fetchone()
    for name in (
        "key_bits",
        "fingerprint_bits",
        "previous_key_bits",
        "previous_fingerprint_bits",
    ):
        assert len(row[name]) == store._tombstone_bytes
