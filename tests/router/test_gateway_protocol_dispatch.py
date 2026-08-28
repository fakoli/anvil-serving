from __future__ import annotations

import http.client
import io
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.a2a.protocol import A2A_PATH, AGENT_CARD_PATH
from anvil_serving.media.contracts import JobState
from anvil_serving.router.front_door import make_server
from anvil_serving.router.gateway import ProtocolGateway
from anvil_serving.router.config import ConfigError, load, load_server_config
from anvil_serving.router.serve import build_server

from tests.a2a.test_tasks import CALLER, send_request, service
from tests.router.helpers import StaticBackend


CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


@contextmanager
def gateway_server(tmp_path, *, enabled=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tasks = service(tmp_path)
    gateway = (
        ProtocolGateway(
            caller=CALLER,
            tasks=tasks,
            registry=tasks.operations.registry,
            artifacts=tasks.operations.artifacts,
            public_origin="http://127.0.0.1:8080",
        )
        if enabled
        else None
    )
    backend = StaticBackend(["unchanged"])
    httpd = make_server(
        "127.0.0.1", 0, backend, auth_token="secret", gateway=gateway
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[:2], tasks
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(address, method, path, *, body=None, token="secret", headers=None):
    connection = http.client.HTTPConnection(*address, timeout=5)
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    if encoded is not None:
        request_headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def mcp_request(request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }


def test_gateway_dispatches_scoped_mcp_a2a_and_agent_card(tmp_path):
    with gateway_server(tmp_path) as (address, _tasks):
        status, _, raw = request(address, "POST", "/mcp", body=mcp_request())
        assert status == 200
        tool_names = {
            tool["name"] for tool in json.loads(raw)["result"]["tools"]
        }
        assert "media_workflow_run" in tool_names
        assert "router_manage" not in tool_names

        status, _, raw = request(address, "POST", A2A_PATH, body=send_request())
        assert status == 200
        assert json.loads(raw)["result"]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"

        status, _, raw = request(address, "GET", AGENT_CARD_PATH)
        assert status == 200
        card = json.loads(raw)
        assert card["supportedInterfaces"][0]["url"] == "http://127.0.0.1:8080/a2a"
        assert card["skills"][0]["id"] == "anvil.media.image.generate"


def test_artifact_delivery_is_opaque_scoped_and_range_bounded(tmp_path):
    with gateway_server(tmp_path) as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes",
            workflow_id="image.test",
            workflow_version="v1",
            input_digest="a" * 64,
            idempotency_key="artifact-request",
        )
        artifact = tasks.operations.artifacts.ingest(
            job,
            io.BytesIO(b"\x89PNG\r\n\x1a\ncontent"),
            media_type="image/png",
            max_bytes=1024,
            retention_seconds=60,
        )
        status, headers, raw = request(
            address,
            "GET",
            f"/artifacts/{artifact.id}",
            headers={"Range": "bytes=0-7"},
        )
        assert (status, raw) == (206, b"\x89PNG\r\n\x1a\n")
        assert headers["Content-Range"] == f"bytes 0-7/{artifact.byte_length}"
        status, _, raw = request(address, "GET", "/artifacts/art_not-owned")
        assert status == 404
        assert b"source_path" not in raw


def test_protocol_auth_precedes_dispatch_and_unknown_routes_do_not_fallback(tmp_path):
    with gateway_server(tmp_path) as (address, _tasks):
        status, _, _ = request(
            address, "POST", "/mcp", body={"not": "rpc"}, token="wrong"
        )
        assert status == 401
        status, _, raw = request(address, "POST", "/mcp", body=mcp_request(2))
        assert status == 200
        unknown = mcp_request(3)
        unknown["method"] = "tools/nope"
        status, _, raw = request(address, "POST", "/mcp", body=unknown)
        assert status == 200
        assert json.loads(raw)["error"]["code"] == -32601
        status, _, _ = request(address, "POST", "/not-a-route", body={})
        assert status == 404


def test_a2a_sse_observes_terminal_job_without_owning_execution(tmp_path):
    with gateway_server(tmp_path) as (address, tasks):
        task = tasks.send_message(send_request()["params"], caller=CALLER)["task"]
        tasks.operations.jobs.transition(task["id"], JobState.CANCELED, principal="hermes")
        stream_request = {
            "jsonrpc": "2.0",
            "id": "observe-one",
            "method": "SubscribeToTask",
            "params": {"id": task["id"]},
        }
        status, headers, raw = request(
            address,
            "POST",
            A2A_PATH,
            body=stream_request,
            headers={"Accept": "text/event-stream"},
        )
        assert status == 200
        assert headers["Content-Type"] == "text/event-stream"
        assert b"TASK_STATE_CANCELED" in raw
        assert tasks.operations.jobs.get(task["id"], principal="hermes").state == JobState.CANCELED


def test_enabling_gateway_does_not_change_v1_models_wire(tmp_path):
    with gateway_server(tmp_path / "enabled") as (enabled, _):
        enabled_response = request(enabled, "GET", "/v1/models")
    with gateway_server(tmp_path / "disabled", enabled=False) as (disabled, _):
        disabled_response = request(disabled, "GET", "/v1/models")
    enabled_status, enabled_headers, enabled_body = enabled_response
    disabled_status, disabled_headers, disabled_body = disabled_response
    # HTTP Date is generated at response time and can cross a second boundary.
    enabled_headers.pop("Date", None)
    disabled_headers.pop("Date", None)
    assert (enabled_status, enabled_headers, enabled_body) == (
        disabled_status,
        disabled_headers,
        disabled_body,
    )


def test_media_gateway_config_is_explicit_scoped_and_secret_referenced(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        """
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
media_principal = "hermes"
media_scopes = ["media:read", "media:submit", "media:cancel"]
media_public_origin = "https://gateway.example.test"
""",
        encoding="utf-8",
    )
    config = load_server_config(str(path))
    assert config.media_principal == "hermes"
    assert config.media_scopes == ("media:read", "media:submit", "media:cancel")
    assert config.media_public_origin == "https://gateway.example.test"
    text = path.read_text(encoding="utf-8")
    assert "secret" not in text.lower()

    path.write_text(
        """
[server]
media_principal = "hermes"
media_scopes = ["media:read"]
media_public_origin = "https://gateway.example.test"
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="require auth_env"):
        load_server_config(str(path))


def test_build_server_composes_one_shared_media_service(tmp_path):
    config_path = tmp_path / "router.toml"
    config_path.write_text(
        CONFIG.read_text(encoding="utf-8")
        + """

[server]
auth_env = "ANVIL_ROUTER_TOKEN"
media_principal = "hermes"
media_scopes = ["media:read", "media:submit", "media:cancel"]
media_public_origin = "http://127.0.0.1:8080"
""",
        encoding="utf-8",
    )
    config = load(str(config_path))
    backends = {tier.id: StaticBackend(["ok"]) for tier in config.tiers}
    server = build_server(
        str(config_path),
        port=0,
        backends=backends,
        env={
            "ANVIL_ROUTER_TOKEN": "secret",
            "ANVIL_MEDIA_BACKEND_URL": "http://127.0.0.1:8188",
            "ANVIL_MEDIA_STATE_DB": str(tmp_path / "jobs.sqlite3"),
            "ANVIL_MEDIA_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            "ANVIL_MEDIA_WORKFLOW_REGISTRY": str(
                Path(__file__).resolve().parents[2]
                / "configs"
                / "media"
                / "workflows"
                / "registry.json"
            ),
        },
    )
    try:
        assert server.anvil_gateway.tasks.operations.jobs is server.anvil_gateway.tasks.operations.jobs
        assert server.anvil_gateway.artifacts is server.anvil_gateway.tasks.operations.artifacts
    finally:
        server.server_close()
