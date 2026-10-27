from __future__ import annotations

import http.client
import hashlib
import io
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from anvil_serving import mcp
from anvil_serving.a2a.protocol import A2A_PATH, A2A_VERSION, AGENT_CARD_PATH
from anvil_serving.media.contracts import JobState
from anvil_serving.media.artifacts import MAX_RANGE_BYTES
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
def gateway_server(
    tmp_path, *, enabled=True, caller=CALLER, public_origin="http://127.0.0.1:8080"
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tasks = service(tmp_path)
    gateway = (
        ProtocolGateway(
            caller=caller,
            tasks=tasks,
            registry=tasks.operations.registry,
            artifacts=tasks.operations.artifacts,
            public_origin=public_origin,
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


def test_protocol_get_rejects_framed_body_without_resetting_error(tmp_path):
    with gateway_server(tmp_path) as (address, _tasks):
        for _ in range(10):
            status, headers, raw = request(
                address,
                "GET",
                AGENT_CARD_PATH,
                body={"smuggled": True},
            )
            assert status == 400
            assert headers["Connection"] == "close"
            assert json.loads(raw)["error"]["type"] == "invalid_request"


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


@pytest.mark.parametrize("suffix", ["", "/"])
def test_video_inspection_returns_authenticated_download_link(tmp_path, suffix):
    with gateway_server(
        tmp_path, public_origin="http://127.0.0.1:8080" + suffix
    ) as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes", workflow_id="video.test", workflow_version="v1",
            input_digest="b" * 64, idempotency_key="video-download-link",
        )
        video = b"\x00\x00\x00\x18ftypmp42video-content"
        artifact = tasks.operations.artifacts.ingest(
            job, io.BytesIO(video), media_type="video/mp4", max_bytes=1024,
            retention_seconds=60,
        )
        body = mcp_request()
        body["method"] = "tools/call"
        body["params"].update(
            name="media_artifact_inspect", arguments={"artifact_id": artifact.id}
        )
        status, _, raw = request(
            address, "POST", "/mcp", body=body, headers={"Host": "untrusted.example"}
        )
        result = json.loads(raw)["result"]
        value = result["structuredContent"]["data"]["artifact"]
        url = "http://127.0.0.1:8080/artifacts/" + artifact.id
        assert status == 200 and not result["isError"]
        assert value["downloadUrl"] == url
        assert value["requiresAuthentication"] is True
        assert value["maxRangeBytes"] == MAX_RANGE_BYTES
        link = next(item for item in result["content"] if item["type"] == "resource_link")
        assert (link["uri"], link["mimeType"], link["name"]) == (
            url, "video/mp4", artifact.id + ".mp4"
        )
        assert "secret" not in json.dumps(value)
        path = value["resource"]
        assert request(address, "GET", path, token=None)[0] == 401
        status, _, raw = request(address, "GET", path)
        assert (status, raw) == (200, video)
        status, headers, raw = request(
            address, "GET", path, headers={"Range": "bytes=0-7"}
        )
        assert (status, raw) == (206, video[:8])
        assert headers["Content-Range"] == f"bytes 0-7/{len(video)}"

        other_job, _ = tasks.operations.jobs.create(
            principal="another", workflow_id="video.test", workflow_version="v1",
            input_digest="c" * 64, idempotency_key="other-video-download-link",
        )
        other = tasks.operations.artifacts.ingest(
            other_job, io.BytesIO(video), media_type="video/mp4", max_bytes=1024,
            retention_seconds=60,
        )
        body["params"]["arguments"]["artifact_id"] = other.id
        _, _, raw = request(address, "POST", "/mcp", body=body)
        assert json.loads(raw)["result"]["isError"]
        assert b"downloadUrl" not in raw


def test_cross_principal_inspection_keeps_http_ownership_boundary(tmp_path):
    caller = {"principal": "operator", "scopes": ["media:read", "operator:media"]}
    with gateway_server(tmp_path, caller=caller) as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes", workflow_id="video.test", workflow_version="v1",
            input_digest="d" * 64, idempotency_key="cross-principal-video",
        )
        artifact = tasks.operations.artifacts.ingest(
            job, io.BytesIO(b"\x00\x00\x00\x18ftypmp42video"),
            media_type="video/mp4", max_bytes=1024, retention_seconds=60,
        )
        body = mcp_request()
        body["method"] = "tools/call"
        body["params"].update(
            name="media_artifact_inspect",
            arguments={"artifact_id": artifact.id, "owner": "hermes"},
        )
        _, _, raw = request(address, "POST", "/mcp", body=body)
        result = json.loads(raw)["result"]
        assert not result["isError"]
        assert "downloadUrl" not in result["structuredContent"]["data"]["artifact"]
        assert all(item["type"] != "resource_link" for item in result["content"])
        assert request(address, "GET", "/artifacts/" + artifact.id)[0] == 404


def test_large_video_link_downloads_in_authenticated_bounded_ranges(tmp_path):
    with gateway_server(tmp_path) as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes", workflow_id="video.test", workflow_version="v1",
            input_digest="e" * 64, idempotency_key="large-video-link",
        )
        video = b"\x00\x00\x00\x18ftypmp42" + b"x" * MAX_RANGE_BYTES
        artifact = tasks.operations.artifacts.ingest(
            job, io.BytesIO(video), media_type="video/mp4", max_bytes=len(video),
            retention_seconds=60,
        )
        body = mcp_request()
        body["method"] = "tools/call"
        body["params"].update(
            name="media_artifact_inspect", arguments={"artifact_id": artifact.id}
        )
        _, _, raw = request(address, "POST", "/mcp", body=body)
        value = json.loads(raw)["result"]["structuredContent"]["data"]["artifact"]
        path = value["resource"]
        assert request(address, "GET", path)[0] == 416
        digest = hashlib.sha256()
        size = 0
        for start in range(0, value["byteLength"], value["maxRangeBytes"]):
            end = min(start + value["maxRangeBytes"], value["byteLength"]) - 1
            status, _, chunk = request(
                address, "GET", path, headers={"Range": f"bytes={start}-{end}"}
            )
            assert status == 206 and len(chunk) == end - start + 1
            size += len(chunk)
            digest.update(chunk)
        assert size == value["byteLength"]
        assert digest.hexdigest() == value["sha256"]


def test_signed_links_reject_device_key_policy_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("ANVIL_MEDIA_SIGNED_DOWNLOADS", "1")
    tasks = service(tmp_path)
    gateway = ProtocolGateway(caller=CALLER, tasks=tasks, registry=tasks.operations.registry,
                              artifacts=tasks.operations.artifacts, public_origin="http://127.0.0.1:8080")
    with pytest.raises(ValueError, match="dedicated static-auth gateway"):
        from anvil_serving.router.front_door import _make_handler
        _make_handler(StaticBackend(["ok"]), None, (), auth_token="secret",
                      gateway=gateway, api_keys=object())


def test_signed_video_links_open_directly_and_stream_large_browser_ranges(tmp_path, monkeypatch):
    import urllib.parse

    monkeypatch.setenv("ANVIL_MEDIA_SIGNED_DOWNLOADS", "1")
    with gateway_server(tmp_path, public_origin="http://127.0.0.1:8080") as (address, tasks):
        job, _ = tasks.operations.jobs.create(
            principal="hermes", workflow_id="video.test", workflow_version="v1",
            input_digest="f" * 64, idempotency_key="signed-video",
        )
        video = b"\x00\x00\x00\x18ftypmp42" + b"x" * (MAX_RANGE_BYTES + 17)
        artifact = tasks.operations.artifacts.ingest(
            job, io.BytesIO(video), media_type="video/mp4", max_bytes=len(video), retention_seconds=60,
        )
        read_sizes = []
        original_read = tasks.operations.artifacts.read

        def bounded_read(*args, **kwargs):
            read_sizes.append(kwargs["end"] - kwargs["start"] + 1)
            return original_read(*args, **kwargs)

        monkeypatch.setattr(tasks.operations.artifacts, "read", bounded_read)
        body = mcp_request()
        body["method"] = "tools/call"
        body["params"].update(name="media_artifact_inspect", arguments={"artifact_id": artifact.id})
        _, _, raw = request(address, "POST", "/mcp", body=body)
        result = json.loads(raw)["result"]
        value = result["structuredContent"]["data"]["artifact"]
        assert value["requiresAuthentication"] is False and value["downloadAuthorization"] == "signed-url"
        assert value["downloadExpiresAt"] and "secret" not in value["downloadUrl"]
        assert any(x.get("uri") == value["downloadUrl"] for x in result["content"])
        parsed = urllib.parse.urlsplit(value["downloadUrl"])
        path = parsed.path + "?" + parsed.query
        assert request(address, "GET", value["resource"], token=None)[0] == 401
        status, headers, data = request(address, "GET", path, token=None)
        assert status == 200 and hashlib.sha256(data).hexdigest() == value["sha256"]
        assert headers["Referrer-Policy"] == "no-referrer" and "no-store" in headers["Cache-Control"]
        assert max(read_sizes) <= 1024 * 1024
        status, headers, data = request(address, "HEAD", path, token=None)
        assert status == 200 and not data and int(headers["Content-Length"]) == len(video)
        for selected, start, end in [("bytes=7-15", 7, 15), ("bytes=16-", 16, len(video)-1),
                                     ("bytes=-8", len(video)-8, len(video)-1)]:
            status, headers, data = request(address, "GET", path, token=None, headers={"Range": selected})
            assert status == 206 and data == video[start:end+1]
            assert headers["Content-Range"] == f"bytes {start}-{end}/{len(video)}"
        status, headers, _ = request(address, "GET", path, token=None, headers={"Range": f"bytes={len(video)}-"})
        assert status == 416 and headers["Content-Range"] == f"bytes */{len(video)}"
        assert request(address, "GET", path.replace(artifact.id, "art_other"), token=None)[0] == 401
        assert request(address, "GET", path + "&expires=1", token=None)[0] == 401
        assert request(address, "POST", "/mcp?" + parsed.query, body=mcp_request(), token=None)[0] == 401


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


def test_media_only_gateway_starts_without_a_chat_backend(tmp_path):
    config_path = tmp_path / "router.toml"
    config_path.write_text(
        """
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
media_principal = "hermes"
media_scopes = ["media:read", "media:submit", "media:cancel"]
media_public_origin = "http://127.0.0.1:8080"

[router]
""",
        encoding="utf-8",
    )
    server = build_server(
        str(config_path),
        port=0,
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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.anvil_tiers == ()
        address = server.server_address[:2]
        status, _, raw = request(address, "POST", "/mcp", body=mcp_request())
        assert status == 200
        tools = {tool["name"] for tool in json.loads(raw)["result"]["tools"]}
        assert len(tools) == 8
        assert "media_workflow_run" in tools
        status, _, raw = request(address, "GET", "/v1/models")
        assert (status, json.loads(raw)) == (200, {"object": "list", "data": []})
        status, _, raw = request(address, "GET", "/v1/health/tiers")
        assert (status, json.loads(raw)) == (200, {"tiers": []})
        status, _, _ = request(
            address,
            "POST",
            "/v1/chat/completions",
            body={"model": "llm.unknown", "messages": []},
        )
        assert status == 404
        status, _, _ = request(address, "POST", "/mcp", body=mcp_request(), token="wrong")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
            self.base_url = "http://127.0.0.1:8188"

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
    forwarded_signed_mode = []
    original_make_server = router_serve.make_server

    def capture_signed_mode(*args, **kwargs):
        forwarded_signed_mode.append(kwargs.get("media_signed_downloads"))
        return original_make_server(*args, **kwargs)

    monkeypatch.setattr(router_serve, "make_server", capture_signed_mode)
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
            "ANVIL_MEDIA_SIGNED_DOWNLOADS": "1",
        },
    )
    worker = server.anvil_media_worker
    assert forwarded_signed_mode == ["1"]
    reconciled_after_submit = threading.Event()
    submitted_id = None
    reconcile_once = worker.reconcile_once

    def observe_reconcile_once():
        observed_submission = submitted_id
        result = reconcile_once()
        if observed_submission is not None:
            reconciled_after_submit.set()
        return result

    # Observe the server-owned daemon after the durable submission; the test
    # never drives reconciliation from its own thread.
    worker.reconcile_once = observe_reconcile_once
    try:
        submitted = server.anvil_gateway.tasks.send_message(
            send_request()["params"], caller=CALLER
        )["task"]
        submitted_id = submitted["id"]
        assert reconciled_after_submit.wait(10), "media worker did not reconcile the submitted job"
        job = server.anvil_gateway.tasks.operations.jobs.get(
            submitted["id"], principal="hermes"
        )
        assert job.state == JobState.COMPLETED
        assert len(job.artifacts) == 1
        assert server.anvil_gateway.artifacts.read(
            job.artifacts[0].id, principal="hermes"
        ).data.startswith(b"\x89PNG")
        assert worker.is_alive is True
    finally:
        server.server_close()
    assert worker.is_alive is False
