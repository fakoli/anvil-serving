from __future__ import annotations

from anvil_serving import mcp
from anvil_serving.control_plane.mcp.tools import media
from anvil_serving.control_plane.mcp.tools import media_worker
from anvil_serving.media.comfyui import ComfyUIClient
from anvil_serving.media.contracts import JobState
from anvil_serving.media.jobs import MediaJobStore
from tests.a2a.test_tasks import CALLER as MEDIA_CALLER
from tests.a2a.test_tasks import cold_service


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
    assert "resumeBundle" not in result["data"]


def test_profileless_cold_resume_bundle_can_replay_and_cancel(tmp_path):
    tasks = cold_service(tmp_path)
    arguments = {
        "workflow_id": "image.test",
        "version": "v1",
        "parameters": {"prompt": "profileless mountain"},
        "idempotency_key": "profileless-cold-request",
    }

    with media.service_context(tasks.operations, tasks.backend):
        submitted = mcp.call_tool(
            "media_workflow_run",
            arguments,
            caller=MEDIA_CALLER,
            audience="media",
        )
        assert submitted["ok"] is True
        bundle = submitted["data"]["resumeBundle"]
        assert bundle["quality_profile"] == ""
        assert submitted["data"]["job"]["state"] == "awaiting_approval"

        full_bundle_replay = mcp.call_tool(
            "media_workflow_run",
            bundle,
            caller=MEDIA_CALLER,
            audience="media",
        )
        assert full_bundle_replay["ok"] is False
        assert full_bundle_replay["error"]["code"] == "bad_argument"
        assert full_bundle_replay["error"]["details"]["fields"] == [
            "approval_transaction_id",
            "job_id",
        ]

        replay_arguments = {
            name: bundle[name]
            for name in (
                "workflow_id",
                "version",
                "parameters",
                "quality_profile",
                "idempotency_key",
            )
        }
        replayed = mcp.call_tool(
            "media_workflow_run",
            replay_arguments,
            caller=MEDIA_CALLER,
            audience="media",
        )
        assert replayed["ok"] is True
        assert replayed["data"]["created"] is False
        assert replayed["data"]["job"]["id"] == bundle["job_id"]
        assert replayed["data"]["resumeBundle"] == bundle

        canceled = mcp.call_tool(
            "media_job_cancel",
            {"job_id": bundle["job_id"]},
            caller=MEDIA_CALLER,
            audience="media",
        )

    assert canceled["ok"] is True
    assert canceled["data"]["canceled"] is True
    assert canceled["data"]["job"]["state"] == "canceled"
    assert tasks.operations.jobs.get(
        bundle["job_id"], principal="hermes"
    ).state == JobState.CANCELED


def test_cross_principal_scope_is_required_before_job_lookup(monkeypatch):
    monkeypatch.setattr(media, "_services", lambda: (_ for _ in ()).throw(AssertionError("looked up job")))
    denied = mcp.call_tool(
        "media_job_status",
        {"job_id": "job_opaque_identifier", "owner": "another"},
        caller=READ,
    )
    assert denied["error"]["code"] == "scope_denied"


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

    def remote(controller_url, request, token, *, timeout):
        name = request["params"]["name"]
        arguments = request["params"]["arguments"]
        observed.append(
            (
                controller_url,
                name,
                arguments,
                token,
                request["params"]["_meta"],
                timeout,
            )
        )
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
    assert all(call[2]["manifest_from_operator_home"] is True for call in observed)
    assert all(call[3] == "resource-secret" for call in observed)
    assert observed[1][2]["timeout_seconds"] == 1800
    assert all(
        call[4]["io.modelcontextprotocol/protocolVersion"] == mcp.PROTOCOL_VERSION
        for call in observed
    )
    assert [call[5] for call in observed] == [30, 30]
    waiting = MediaJobStore(state_path).get(job.id, principal="hermes")
    assert waiting.state == JobState.AWAITING_APPROVAL
    assert waiting.approval["operatorAction"]["arguments"]["manifest"] == (
        "serves.comfyui.toml"
    )


def test_media_resource_controller_timeout_covers_bounded_child_operation(
    monkeypatch,
):
    monkeypatch.setenv(
        "ANVIL_MEDIA_RESOURCE_CONTROLLER_URL", "http://127.0.0.1:8766"
    )
    monkeypatch.setenv("ANVIL_MEDIA_RESOURCE_CONTROLLER_TOKEN", "resource-secret")
    observed = []

    def remote(controller_url, request, token, *, timeout):
        observed.append(
            (
                request["params"]["name"],
                timeout,
                request["params"]["arguments"].get("timeout_seconds"),
            )
        )
        return {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {"structuredContent": {"ok": True, "data": {}}},
        }

    monkeypatch.setattr(media_worker, "remote_controller_request", remote)

    media_worker._resource_manage(
        {
            "action": "up",
            "confirm": True,
            "dry_run": False,
        },
    )
    media_worker._resource_tool(
        "serves_logs",
        {"timeout_seconds": 600},
    )

    assert observed == [
        ("serves_manage", 1805, 1800),
        ("serves_logs", 605, 600),
    ]


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
