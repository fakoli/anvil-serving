from __future__ import annotations

import http.client
import io
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.a2a.protocol import A2A_PATH, A2A_VERSION, AGENT_CARD_PATH
from anvil_serving.media.contracts import JobState
from anvil_serving.media.backends import BackendOutput, BackendStatus
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.router.front_door import make_server
from anvil_serving.router.gateway import ProtocolGateway
from anvil_serving.router.config import ConfigError, load, load_server_config
from anvil_serving.router.serve import build_server

from tests.a2a.test_tasks import CALLER, Registry, send_request, service
from tests.router.helpers import StaticBackend


CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


@contextmanager
def gateway_server(tmp_path, *, enabled=True, caller=CALLER):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tasks = service(tmp_path)
    gateway = (
        ProtocolGateway(
            caller=caller,
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


def request(
    address,
    method,
    path,
    *,
    body=None,
    token="secret",
    headers=None,
    a2a_version=A2A_VERSION,
):
    connection = http.client.HTTPConnection(*address, timeout=5)
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = dict(headers or {})
    if path == A2A_PATH and a2a_version is not None:
        request_headers["A2A-Version"] = a2a_version
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


@pytest.mark.parametrize(
    ("version", "requested"),
    [
        (None, "0.3"),
        ("", "0.3"),
        ("0.3", "0.3"),
        ("1.1", "1.1"),
        ("1.0.0", "1.0.0"),
    ],
)
def test_a2a_rejects_missing_legacy_and_unsupported_versions_without_dispatch(
    tmp_path, version, requested
):
    with gateway_server(tmp_path) as (address, tasks):
        status, headers, raw = request(
            address,
            "POST",
            A2A_PATH,
            body=send_request(),
            a2a_version=version,
        )
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        response = json.loads(raw)
        assert response["id"] == 1
        assert response["error"]["code"] == -32009
        detail = response["error"]["data"][0]
        assert detail["reason"] == "VERSION_NOT_SUPPORTED"
        assert detail["metadata"] == {
            "requestedVersion": requested,
            "supportedVersions": A2A_VERSION,
        }
        assert tasks.operations.jobs.nonterminal() == []


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


def test_artifact_delivery_requires_media_read_scope(tmp_path):
    caller = {"principal": "hermes", "scopes": ["media:submit"]}
    with gateway_server(tmp_path, caller=caller) as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes",
            workflow_id="image.test",
            workflow_version="v1",
            input_digest="a" * 64,
            idempotency_key="artifact-scope-request",
        )
        artifact = tasks.operations.artifacts.ingest(
            job,
            io.BytesIO(b"\x89PNG\r\n\x1a\ncontent"),
            media_type="image/png",
            max_bytes=1024,
            retention_seconds=60,
        )
        status, _, raw = request(address, "GET", f"/artifacts/{artifact.id}")
        assert status == 403
        assert json.loads(raw)["error"]["type"] == "scope_denied"


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


def test_a2a_sse_rejects_terminal_task_subscription_without_mutation(tmp_path):
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
        assert headers["Content-Type"] == "application/json"
        error = json.loads(raw)["error"]
        assert error["code"] == -32004
        assert error["data"][0]["reason"] == "UNSUPPORTED_OPERATION"
        assert tasks.operations.jobs.get(task["id"], principal="hermes").state == JobState.CANCELED


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"id": "job_missing-task-id", "metadata": {}}, -32602),
        ({"tenant": "undeclared", "id": "job_missing-task-id"}, -32004),
    ],
)
def test_a2a_subscription_uses_normative_1_0_parameter_shape(
    tmp_path, params, code
):
    with gateway_server(tmp_path) as (address, _tasks):
        status, headers, raw = request(
            address,
            "POST",
            A2A_PATH,
            body={
                "jsonrpc": "2.0",
                "id": "observe-shape",
                "method": "SubscribeToTask",
                "params": params,
            },
            headers={"Accept": "text/event-stream"},
        )
        assert status == 200
        assert headers["Content-Type"] == "application/json"
        assert json.loads(raw)["error"]["code"] == code


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


def test_media_lifecycle_preview_uses_bounded_controller_tool_call(monkeypatch):
    import anvil_serving.router.serve as router_serve

    observed = {}

    def remote(controller_url, request, token):
        observed.update(
            {"controller_url": controller_url, "request": request, "token": token}
        )
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "structuredContent": {
                    "ok": True,
                    "data": {
                        "transactionId": "preview-transaction",
                        "service": "media-worker",
                        "action": "prepare",
                        "humanRequired": True,
                        "manifest": "serves.comfyui.toml",
                    },
                }
            },
        }

    monkeypatch.setattr(router_serve, "remote_controller_request", remote)
    preview = router_serve._media_lifecycle_preview(
        "http://127.0.0.1:8765",
        "controller-secret",
    )
    receipt = preview("job_0123456789abcdef", "hermes", "media-worker")
    assert receipt["humanRequired"] is True
    assert observed["controller_url"] == "http://127.0.0.1:8765"
    assert observed["token"] == "controller-secret"
    request = observed["request"]
    assert request["params"]["name"] == "media_worker_prepare"
    assert request["params"]["arguments"] == {
        "job_id": "job_0123456789abcdef",
        "principal": "hermes",
        "service": "media-worker",
        "dry_run": True,
        "confirm": False,
        "human_approved": False,
    }
    assert receipt["manifest"] == "serves.comfyui.toml"
    assert request["params"]["_meta"][
        "io.modelcontextprotocol/protocolVersion"
    ] == mcp.PROTOCOL_VERSION


def test_media_controller_url_and_token_are_an_atomic_configuration(tmp_path):
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
    with pytest.raises(ConfigError, match="must be configured together"):
        build_server(
            str(config_path),
            port=0,
            backends={tier.id: StaticBackend(["ok"]) for tier in config.tiers},
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
                "ANVIL_MEDIA_CONTROLLER_URL": "http://127.0.0.1:8765",
            },
        )


def test_build_server_reconciles_submissions_and_stops_worker(tmp_path, monkeypatch):
    import anvil_serving.router.serve as router_serve

    class CompletingBackend:
        def __init__(self, _url):
            pass

        def compatibility(self, workflow, *, qualification=False):
            return WorkflowCompatibility(workflow.id, workflow.version, True, True)

        def submit(self, workflow, *, job_id):
            return "prompt-completed"

        def history(self, prompt_id):
            return BackendStatus(
                prompt_id,
                "completed",
                outputs=(BackendOutput("1", "private-output.png"),),
            )

        def fetch_output(self, output, *, max_bytes):
            payload = b"\x89PNG\r\n\x1a\nproduction-output"
            assert len(payload) <= max_bytes
            return payload

        def delete_queued_prompt(self, prompt_id):
            raise AssertionError("completed prompt must not be deleted")

        def interrupt_exclusive_prompt(self):
            raise AssertionError("completed prompt must not be interrupted")

    monkeypatch.setattr(router_serve, "WorkflowRegistry", lambda _path: Registry())
    monkeypatch.setattr(router_serve, "ComfyUIClient", CompletingBackend)
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
    server = build_server(
        str(config_path),
        port=0,
        backends={tier.id: StaticBackend(["ok"]) for tier in config.tiers},
        env={
            "ANVIL_ROUTER_TOKEN": "secret",
            "ANVIL_MEDIA_BACKEND_URL": "http://127.0.0.1:8188",
            "ANVIL_MEDIA_STATE_DB": str(tmp_path / "jobs.sqlite3"),
            "ANVIL_MEDIA_ARTIFACT_ROOT": str(tmp_path / "artifacts"),
            "ANVIL_MEDIA_WORKFLOW_REGISTRY": str(tmp_path / "registry.json"),
        },
    )
    worker = server.anvil_media_worker
    try:
        submitted = server.anvil_gateway.tasks.send_message(
            send_request()["params"], caller=CALLER
        )["task"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = server.anvil_gateway.tasks.operations.jobs.get(
                submitted["id"], principal="hermes"
            )
            if job.state == JobState.COMPLETED:
                break
            time.sleep(0.02)
        assert job.state == JobState.COMPLETED
        assert len(job.artifacts) == 1
        assert server.anvil_gateway.artifacts.read(
            job.artifacts[0].id, principal="hermes"
        ).data.startswith(b"\x89PNG")
        assert worker.is_alive is True
    finally:
        server.server_close()
    assert worker.is_alive is False
