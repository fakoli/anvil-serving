"""Tests for the ADR-0014 controller transport.

Hermetic: live HTTP tests bind only to 127.0.0.1:0 with fake MCP functions.
"""

import contextlib
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

import pytest

from anvil_serving import cli, controller, controller_diagnostics, mcp, transports
from anvil_serving.control_plane.controller import cli as controller_cli
from anvil_serving.control_plane.mcp import protocol as mcp_protocol


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
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
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


def test_controller_status_parser_accepts_repeatable_required_operations():
    args = controller._build_parser().parse_args(
        [
            "status",
            "--require-operation",
            "host_summary",
            "--require-operation",
            "router-status",
        ]
    )

    assert args.require_operation == ["host_summary", "router-status"]


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
            "/mcp",
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=auth,
        )
        assert status == 200
        assert body["result"]["resultType"] == "complete"
        assert body["result"]["ttlMs"] == 30000
        assert body["result"]["cacheScope"] == "private"
        assert body["result"]["tools"][0]["name"] == "fake"
        assert TOKEN not in raw.decode("utf-8")

        status, _, body, raw = _request(
            host,
            port,
            "POST",
            "/mcp",
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
        assert body["result"]["resultType"] == "complete"
        assert body["result"]["_meta"]["io.modelcontextprotocol/serverInfo"] == mcp.SERVER_INFO
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

    assert any(a["operation"] == "mcp" and a["tool"] == "fake" for a in audits)
    assert any(a["operation"] == "tools/call" and a["confirm"] is True for a in audits)
    assert TOKEN not in json.dumps(audits)


def test_controller_mcp_2026_discovery_is_the_only_supported_lifecycle():
    with running_controller() as (host, port):
        status, _, discovered, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body={"jsonrpc": "2.0", "id": "discover", "method": "server/discover"},
        )
        assert status == 200
        assert discovered["result"]["supportedVersions"] == [mcp.PROTOCOL_VERSION]
        assert discovered["result"]["resultType"] == "complete"
        assert discovered["result"]["_meta"]["io.modelcontextprotocol/serverInfo"] == (
            mcp.SERVER_INFO
        )

        status, _, legacy, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body={"jsonrpc": "2.0", "id": "legacy", "method": "initialize"},
        )
        assert status == 404
        assert legacy["error"] == {"code": -32601, "message": "method not found"}


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
            "/mcp",
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
            "/mcp",
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
                b"POST /mcp HTTP/1.1\r\n"
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
                b"POST /mcp HTTP/1.1\r\n"
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
            "/mcp",
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
            "/mcp",
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
            "/mcp",
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
                "/mcp",
                body={
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": value,
                },
            )
            assert status == 400
            assert body["error"]["code"] == -32602
    assert calls == []


def test_deep_json_parse_failure_stays_a_structured_client_error():
    def fail_deep_json(_value):
        raise RecursionError("too deep")

    with running_controller(json_loads_func=fail_deep_json) as (host, port):
        status, _, body, _ = _request(host, port, "POST", "/tools/call", body={})
    assert status == 400
    assert body["error"]["code"] == "invalid_json"


def test_controller_facade_reexports_supported_surface():
    expected = {
        "BindAssessment",
        "BindSafetyError",
        "ControllerError",
        "OperationStore",
        "main",
        "make_handler",
        "make_server",
        "resolve_auth_token",
        "serve",
        "status",
        "validate_bind_safety",
    }

    assert expected <= set(controller.__all__)
    assert controller.OperationStore.__module__.endswith(".controller.store")
    assert controller.make_handler.__module__.endswith(".controller.http")
    assert controller.make_server.__module__.endswith(".controller.server")
    assert controller.status.__module__.endswith(".controller.cli")


@pytest.mark.parametrize(
    "imports",
    [
        ("anvil_serving.controller", "anvil_serving.mcp"),
        ("anvil_serving.mcp", "anvil_serving.controller"),
    ],
)
def test_controller_and_mcp_import_without_cycles_in_either_order(imports):
    code = "; ".join(f"import {module}" for module in imports)

    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


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


def test_persisted_sanitizer_handles_tuples_and_token_bearing_dict_keys():
    secret = "known-controller-token"
    safe = controller._sanitize_persisted_value(
        {f"prefix-{secret}-suffix": (secret, {secret: "value"})},
        secret,
    )

    assert secret not in json.dumps(safe)


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


def test_bind_safety_accepts_dotenv_backed_token(token_only_in_config_env):
    assessment = controller.validate_bind_safety("127.0.0.1")
    assert assessment.requires_auth is True


def test_bind_safety_explicit_env_stays_hermetic(token_only_in_config_env):
    with pytest.raises(controller.BindSafetyError) as exc:
        controller.validate_bind_safety("127.0.0.1", env={})
    assert exc.value.code == "auth_token_required"
    assert exc.value.details["sources_checked"][0].startswith("environment variable ")


def test_resolve_auth_token_missing_names_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-user"))
    monkeypatch.delenv("ANVIL_CONTROLLER_TOKEN", raising=False)
    with pytest.raises(controller.ControllerError) as exc:
        controller.resolve_auth_token(required=True)
    assert exc.value.code == "auth_token_missing"
    sources = exc.value.details["sources_checked"]
    assert sources[0] == "environment variable ANVIL_CONTROLLER_TOKEN"
    assert len(sources) == 3


def test_controller_status_succeeds_with_dotenv_only_token(token_only_in_config_env):
    with running_controller(env=None) as (host, port):
        exit_code = controller.status(
            "http://%s:%s" % (host, port), max_response_bytes=1024 * 1024
        )
    assert exit_code == 0


def test_controller_status_refusal_lists_checked_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty-user"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "empty-user"))
    monkeypatch.delenv("ANVIL_CONTROLLER_TOKEN", raising=False)
    exit_code = controller.status("http://127.0.0.1:1")
    captured = capsys.readouterr()
    assert exit_code == 3
    assert "ANVIL_CONTROLLER_TOKEN" in captured.err
    assert ".env" in captured.err


def test_controller_status_injected_environment_stays_hermetic(token_only_in_config_env, capsys):
    exit_code = controller.status("http://127.0.0.1:1", environment={})
    captured = capsys.readouterr()
    assert exit_code == 3
    assert "unset or empty" in captured.err


# --- ADR-0033: boot reconciliation of orphaned operations ------------------


def test_recover_interrupted_fails_orphaned_running_records(tmp_path):
    db_path = str(tmp_path / "operations.sqlite3")
    store = controller.OperationStore(db_path)
    assert store.claim("orphan-1", "f" * 64, "request-1")[0] == "claimed"

    recovered = controller.OperationStore(db_path).recover_interrupted()
    assert [item["key"] for item in recovered] == ["orphan-1"]
    assert recovered[0]["request_id"] == "request-1"

    record = controller.OperationStore(db_path).lookup("orphan-1")
    assert record["status"] == "failed"
    assert record["error"]["code"] == "operation_interrupted"
    assert record["response"]["ok"] is False


def test_recover_interrupted_skips_fresh_lease_and_active_keys(tmp_path):
    db_path = str(tmp_path / "operations.sqlite3")
    store = controller.OperationStore(db_path)
    assert store.claim("leased", "a" * 64, "request-1")[0] == "claimed"
    store._write_lease("leased")

    # Fresh lease within the grace window: not an orphan.
    assert controller.OperationStore(db_path).recover_interrupted() == []
    assert controller.OperationStore(db_path).lookup("leased")["status"] == "running"

    # Stale lease (grace forced to zero): recovered.
    recovered = controller.OperationStore(db_path).recover_interrupted(grace_seconds=0)
    assert [item["key"] for item in recovered] == ["leased"]

    # In-process execution protects the key even without a lease.
    store2 = controller.OperationStore(db_path)
    assert store2.claim("active", "b" * 64, "request-2")[0] == "claimed"
    store2._active_keys.add("active")
    assert store2.recover_interrupted(grace_seconds=0) == []


def test_claim_lazily_fails_orphaned_running_record(tmp_path):
    db_path = str(tmp_path / "operations.sqlite3")
    fingerprint = "c" * 64
    store = controller.OperationStore(db_path)
    assert store.claim("lazy-orphan", fingerprint, "request-1")[0] == "claimed"

    # A later process replaying the key sees a typed failure, not a stale 202.
    disposition, record = controller.OperationStore(db_path).claim(
        "lazy-orphan", fingerprint, "request-2"
    )
    assert disposition == "existing"
    assert record["status"] == "failed"
    assert record["error"]["code"] == "operation_interrupted"


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


def test_controller_audit_log_unwritable_is_boot_error(tmp_path):
    missing_dir = tmp_path / "absent" / "audit.jsonl"
    with pytest.raises(controller.ControllerError) as exc:
        controller.make_server(
            "127.0.0.1",
            0,
            allow_unauthenticated_loopback=True,
            idempotency_db_path=str(tmp_path / "operations.sqlite3"),
            audit_log_path=str(missing_dir),
        )
    assert exc.value.code == "audit_log_unwritable"


def test_file_audit_logger_rotates_once(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = controller.FileAuditLogger(str(path), max_bytes=1024)
    for index in range(64):
        logger({"event": "test", "index": index, "padding": "x" * 32})
    assert path.exists()
    assert (tmp_path / "audit.jsonl.1").exists()


def test_controller_health_asserts_node_identity_when_declared():
    with running_controller(node_id="fakoli-dark") as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/health")
    assert status == 200
    assert body["node"] == "fakoli-dark"

    with running_controller() as (host, port):
        status, _, body, _ = _request(host, port, "GET", "/health")
    assert status == 200
    assert "node" not in body


def test_expected_node_transport_accepts_actual_loopback_controller_health():
    calls = []
    audits = []

    def fake_call_tool(name, arguments=None):
        calls.append((name, arguments))
        return {"ok": True, "data": {"observed": True}}

    with running_controller(
        auth_token_env="ANVIL_CONTROLLER_TOKEN",
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        allow_unauthenticated_loopback=False,
        node_id="fakoli-dark",
        call_tool_func=fake_call_tool,
        audit_logger=audits.append,
    ) as (host, port):
        transport = transports.ControllerTransport(
            "http://%s:%s" % (host, port),
            auth_env="ANVIL_CONTROLLER_TOKEN",
            allowed_operations=("router-status",),
            environment={"ANVIL_CONTROLLER_TOKEN": TOKEN},
            expected_node="fakoli-dark",
        )
        first = transport.execute(transports.Operation("router-status", {}))
        second = transport.execute(transports.Operation("router-status", {}))

    for result in (first, second):
        assert result.data["ok"] is True
        assert result.data["data"] == {"observed": True}
        assert transports._REQUEST_ID_RE.fullmatch(result.data["request_id"])
    assert calls == [("router_status", {}), ("router_status", {})]
    assert [record["operation"] for record in audits].count("health") == 1
    assert [record["operation"] for record in audits].count("tools/call") == 2


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


def _diagnostic_inspect_bytes():
    return json.dumps(
        {
            "container_id": _DIAGNOSTIC_CONTAINER_ID,
            "running": True,
            "exit_code": 0,
            "health": "healthy",
            "compose_service": "controller",
            "configured_bindings": {
                "8765/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18765"}],
            },
            "observed_bindings": {"8765/tcp": None},
        },
        separators=(",", ":"),
    ).encode("utf-8")


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


def test_controller_diagnostics_legacy_operator_runs_real_handlers(monkeypatch):
    inspect_bytes = _diagnostic_inspect_bytes()
    private_value = "credential-shaped-private-value"
    capture = _DiagnosticCaptureSpy(
        inspect_bytes,
        inspect_bytes,
        json.dumps(
            {
                "operation": "tools/call",
                "status": 200,
                "private_detail": private_value,
            },
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    _install_diagnostic_capture(monkeypatch, capture)

    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        allowed_operations=("controller_inspect", "controller_logs"),
    ) as (host, port):
        status, _, inspected, inspected_raw = _call_diagnostic(
            host, port, "controller_inspect", TOKEN
        )
        assert status == 200 and inspected["ok"] is True
        assert inspected["data"] == {
            "schema_version": "controller-diagnostics/v1",
            "kind": "inspect",
            "state": "ok",
            "error_code": None,
            "container_id": _DIAGNOSTIC_CONTAINER_ID,
            "truncated": False,
            "running": True,
            "exit_code": 0,
            "health": "healthy",
            "configured_bindings": [
                {"container_port": 8765, "host_port": 18765, "bind_class": "loopback"},
            ],
            "observed_bindings": [],
        }
        status, _, logged, logged_raw = _call_diagnostic(host, port, "controller_logs", TOKEN)
        assert status == 200 and logged["ok"] is True
        assert logged["data"] == {
            "schema_version": "controller-diagnostics/v1",
            "kind": "logs",
            "state": "ok",
            "error_code": None,
            "container_id": _DIAGNOSTIC_CONTAINER_ID,
            "truncated": False,
            "events": [{"operation": "tools/call", "status": 200}],
            "line_count": 1,
            "returned_events": 1,
            "rejected_lines": 0,
            "unknown_fields": 1,
            "unknown_codes": 0,
            "counters_saturated": False,
        }
        assert private_value.encode() not in inspected_raw + logged_raw

    assert len(capture.calls) == 3
    assert capture.calls[0][0][-2:] == (
        controller_diagnostics._INSPECT_TEMPLATE,
        "anvil-serving-controller",
    )
    assert capture.calls[1][0][-2:] == (
        controller_diagnostics._INSPECT_TEMPLATE,
        "anvil-serving-controller",
    )
    assert capture.calls[2][0][-3:] == (
        "--tail",
        "17",
        _DIAGNOSTIC_CONTAINER_ID,
    )


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


def test_mcp_identifying_header_failures_and_body_mismatch_never_dispatch(tmp_path):
    tools = _scoped_tools() + [{"name": "legacy.other", "_meta": {"anvil/requiredScope": None}}]
    calls = []
    with running_controller(
        env={"ANVIL_CONTROLLER_TOKEN": TOKEN},
        list_tools_func=lambda: tools,
        call_tool_func=lambda *args: calls.append(args) or {"ok": True},
    ) as (host, port):
        payload = json.dumps(
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "legacy.operation", "arguments": {}, "_meta": {
                    "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                }},
            }
        ).encode()
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer "
                + TOKEN.encode()
                + b"\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(payload)).encode()
                + b"\r\nMcp-Method: tools/call\r\nMcp-Name: legacy.operation\r\n"
                + b"Mcp-Name: legacy.other\r\n\r\n"
            )
            assert b" 400 " in sock.recv(4096)
        headers = {
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": mcp.PROTOCOL_VERSION,
            "Mcp-Method": "tools/call",
        }
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=json.loads(payload),
            headers=headers,
            mcp_defaults=False,
        )
        assert status == 400 and body["error"]["code"] == mcp_protocol.HEADER_MISMATCH
        headers["Mcp-Name"] = "legacy.other"
        status, _, body, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=json.loads(payload),
            headers=headers,
            mcp_defaults=False,
        )
        assert status == 400 and body["error"]["code"] == mcp_protocol.HEADER_MISMATCH
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
