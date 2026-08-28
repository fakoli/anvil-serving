from __future__ import annotations

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import media
from anvil_serving.control_plane.mcp.tools import media_worker
from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.contracts import JobState
from anvil_serving.media.jobs import MediaJobStore


READ = {"principal": "hermes", "scopes": ["media:read"]}
SUBMIT = {"principal": "hermes", "scopes": ["media:submit"]}


def _call_request(name: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def test_media_discovery_is_deterministic_and_scope_filtered():
    first = mcp.list_tools(caller=READ, audience="media")
    second = mcp.list_tools(caller=READ, audience="media")
    assert first == second
    names = {tool["name"] for tool in first}
    assert "media_capabilities" in names
    assert "media_workflow_run" not in names
    assert "media_job_cancel" not in names
    assert not any(name.startswith("media_worker_") for name in names)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in first)


def test_media_audience_rejects_hidden_operator_tool_calls(monkeypatch):
    called = []
    monkeypatch.setitem(
        mcp.TOOLS["operation_contracts"],
        "handler",
        lambda _args: called.append(True) or {"ok": True},
    )
    response = mcp.handle_request(
        _call_request("operation_contracts"), caller=READ, audience="media"
    )
    assert response["error"]["data"]["code"] == "unknown_tool"
    direct = mcp.call_tool(
        "operation_contracts", {}, caller=READ, audience="media"
    )
    assert direct["error"]["code"] == "unknown_tool"
    assert called == []


def test_scope_denial_precedes_registry_or_downstream_contact(monkeypatch):
    monkeypatch.setattr(media, "_services", lambda: (_ for _ in ()).throw(AssertionError("resolved services")))
    result = mcp.call_tool("media_workflow_list", {}, caller=SUBMIT)
    assert result["ok"] is False
    assert result["error"]["code"] == "scope_denied"


def test_authentication_is_out_of_band_not_a_spoofable_tool_argument(monkeypatch):
    monkeypatch.setattr(media, "_services", lambda: (_ for _ in ()).throw(AssertionError("resolved services")))
    result = mcp.call_tool("media_workflow_list", {})
    assert result["error"]["code"] == "authentication_required"
    spoof = mcp.call_tool("media_workflow_list", {"principal": "hermes"})
    assert spoof["error"]["code"] == "bad_argument"


def test_submit_uses_authenticated_principal_and_normalized_result(monkeypatch):
    seen = {}

    class Operations:
        def workflow_run(self, workflow_id, version, parameters, **kwargs):
            seen.update(
                workflow_id=workflow_id,
                version=version,
                parameters=parameters,
                **kwargs,
            )
            return {"job": {"id": "job_opaque_identifier", "state": "queued"}, "created": True}

    backend = ComfyUIClient("http://127.0.0.1:8188")
    monkeypatch.setattr(media, "_services", lambda: (Operations(), backend))
    result = mcp.call_tool(
        "media_workflow_run",
        {
            "workflow_id": "image.test",
            "version": "v1",
            "parameters": {"prompt": "mountain"},
            "idempotency_key": "request-one",
        },
        caller=SUBMIT,
    )
    assert result["ok"] is True
    assert seen["principal"] == "hermes"
    assert seen["backend"] is backend
    assert "principal" not in result["data"]["job"]


def test_cross_principal_scope_is_required_before_job_lookup(monkeypatch):
    monkeypatch.setattr(media, "_services", lambda: (_ for _ in ()).throw(AssertionError("looked up job")))
    denied = mcp.call_tool(
        "media_job_status",
        {"job_id": "job_opaque_identifier", "owner": "another"},
        caller=READ,
    )
    assert denied["error"]["code"] == "scope_denied"


def test_media_schema_bounds_nested_parameter_objects():
    oversized = {str(index): index for index in range(33)}
    result = mcp.call_tool(
        "media_workflow_run",
        {
            "workflow_id": "image.test",
            "version": "v1",
            "parameters": oversized,
            "idempotency_key": "request-one",
        },
        caller=SUBMIT,
    )
    assert result["error"]["code"] == "bad_argument"


def test_media_worker_manifest_defaults_at_the_resource_owner(monkeypatch):
    observed = {}

    def status(args):
        observed.update(args)
        return {"ok": True, "result": {"serves": []}}

    monkeypatch.setenv("ANVIL_MEDIA_SERVE_MANIFEST", "serves.comfyui.toml")
    monkeypatch.setattr(media_worker, "tool_serves_status", status)
    result = media_worker.tool_media_worker_status({"service": "media-worker"})
    assert result["ok"] is True
    assert observed == {
        "manifest": "serves.comfyui.toml",
        "names": ["media-worker"],
    }


def test_explicit_media_worker_manifest_overrides_resource_owner_default(monkeypatch):
    observed = {}
    monkeypatch.setenv("ANVIL_MEDIA_SERVE_MANIFEST", "default-media.toml")
    monkeypatch.setattr(
        media_worker,
        "tool_serves_status",
        lambda args: observed.update(args) or {"ok": True, "result": {"serves": []}},
    )
    media_worker.tool_media_worker_status(
        {"service": "media-worker", "manifest": "explicit-media.toml"}
    )
    assert observed["manifest"] == "explicit-media.toml"


def test_media_worker_orchestrator_shares_gateway_state_and_proxies_resource_owner(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "jobs.sqlite3"
    store = MediaJobStore(state_path)
    job = store.create(
        principal="hermes",
        workflow_id="image.test",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="cold-request",
    )[0]
    monkeypatch.setenv("ANVIL_MEDIA_STATE_DB", str(state_path))
    monkeypatch.setenv("ANVIL_MEDIA_SERVE_MANIFEST", "serves.comfyui.toml")
    monkeypatch.setenv(
        "ANVIL_MEDIA_RESOURCE_CONTROLLER_URL", "http://127.0.0.1:8766"
    )
    monkeypatch.setenv("ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN", "resource-secret")
    observed = []

    def remote(controller_url, request, token):
        name = request["params"]["name"]
        arguments = request["params"]["arguments"]
        observed.append((controller_url, name, arguments, token, request["params"]["_meta"]))
        if name == "serves_status":
            data = {
                "serves": [
                    {
                        "name": "media-worker",
                        "running": False,
                        "health_status": None,
                    }
                ]
            }
        else:
            assert name == "serves_manage"
            data = {"applied": False, "plan": ["managed-serve-up"]}
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"structuredContent": {"ok": True, "data": data}},
        }

    monkeypatch.setattr(media_worker, "remote_controller_request", remote)
    result = media_worker.tool_media_worker_prepare(
        {
            "job_id": job.id,
            "principal": "hermes",
            "service": "media-worker",
            "dry_run": True,
            "confirm": False,
            "human_approved": False,
        }
    )
    assert result["ok"] is True
    assert result["data"]["manifest"] == "serves.comfyui.toml"
    assert [call[1] for call in observed] == ["serves_status", "serves_manage"]
    assert all(call[0] == "http://127.0.0.1:8766" for call in observed)
    assert all(call[2]["manifest"] == "serves.comfyui.toml" for call in observed)
    assert all(call[3] == "resource-secret" for call in observed)
    assert all(
        call[4]["io.modelcontextprotocol/protocolVersion"] == mcp.PROTOCOL_VERSION
        for call in observed
    )
    waiting = MediaJobStore(state_path).get(job.id, principal="hermes")
    assert waiting.state == JobState.AWAITING_APPROVAL
    assert waiting.approval["operatorAction"]["arguments"]["manifest"] == (
        "serves.comfyui.toml"
    )


def test_media_resource_controller_configuration_is_atomic(monkeypatch):
    monkeypatch.setenv(
        "ANVIL_MEDIA_RESOURCE_CONTROLLER_URL", "http://127.0.0.1:8766"
    )
    monkeypatch.delenv("ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN", raising=False)
    result = mcp.call_tool(
        "media_worker_status",
        {"service": "media-worker"},
    )
    assert result["error"]["code"] == "media_resource_controller_config"


def test_media_worker_manifest_rejects_paths(monkeypatch):
    monkeypatch.setenv("ANVIL_MEDIA_SERVE_MANIFEST", "private/serves.toml")
    result = mcp.call_tool(
        "media_worker_status",
        {"service": "media-worker"},
    )
    assert result["error"]["code"] == "bad_argument"
