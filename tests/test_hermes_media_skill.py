from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

from anvil_serving import mcp
from anvil_serving.media.contracts import JobState
from anvil_serving.router.gateway import ProtocolGateway

from tests.a2a.test_tasks import CALLER, service


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "examples" / "hermes" / "skills" / "anvil-media" / "SKILL.md"
PACKAGED_SKILL = ROOT / "anvil_serving" / "_hermes_skills" / "anvil-media" / "SKILL.md"
README = ROOT / "examples" / "hermes" / "README.md"
PUBLIC_TOOLS = {
    "media_capabilities",
    "media_workflow_list",
    "media_workflow_show",
    "media_workflow_validate",
    "media_workflow_run",
    "media_job_status",
    "media_job_cancel",
    "media_artifact_inspect",
}


def _rpc(name: str, arguments: dict, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def _structured(response: dict) -> dict:
    return response["result"]["structuredContent"]["data"]


def test_packaged_hermes_skill_is_narrow_and_secret_free():
    text = SKILL.read_text(encoding="utf-8")
    assert PACKAGED_SKILL.read_bytes() == SKILL.read_bytes()
    config = README.read_text(encoding="utf-8")
    assert text.startswith("---\nname: anvil-media\n")
    referenced = {
        token.removeprefix("mcp__anvil_media__")
        for token in text.replace("`", " ").split()
        if token.startswith("mcp__anvil_media__media_")
    }
    assert referenced == PUBLIC_TOOLS
    assert "${ANVIL_ROUTER_TOKEN}" in config
    assert "${ANVIL_CONTROLLER_TOKEN}" not in config
    assert "${ANVIL_MEDIA_MCP_URL}" in config
    assert "Bearer " not in config
    assert "media_worker_" not in text
    assert "docker" not in text.casefold()
    assert "http://" not in text and "https://" not in text
    assert "class_type" not in text and "output_nodes" not in text
    assert "Intermediate narration" in text
    assert "until the job is terminal" in text
    assert "Determine the reply language only from the current user request" in text
    assert "An English request receives\nan English response" in text
    assert "Omit internal bookkeeping" in text
    assert "otherwise available workflow returns `backend_unavailable`" in text
    assert "continue to submission" in text
    assert "If the workflow declares no quality profiles" in text
    assert "empty string as its\n   exact `quality_profile` value" in text
    assert "Hermes has no worker-lifecycle authority" in text
    assert "complete resume bundle" in text
    assert 'version: "1.0.5"' in text
    assert "server-returned `resumeBundle` object exactly" in text
    assert "full `idempotency_key`" in text
    assert "compare all seven fields to the returned object" in text
    assert "server omits `resumeBundle` or you cannot reproduce it exactly" in text
    assert "`resume_bundle_incomplete`" in text
    assert "unless every field is also present in the current reply" in text
    assert "caller-generated idempotency key" in text
    assert "Require `created: false` and the same job ID" in text
    assert "Never use\n   `session_search`" in text
    assert 'narration such as "now polling."' in text
    assert '"I will inspect it next" or "now polling"' in text


def test_hermes_skill_makes_fail_closed_resume_cancellation_unambiguous():
    text = SKILL.read_text(encoding="utf-8")
    assert "server omits `resumeBundle` or you cannot reproduce it exactly" in text
    assert "cancel that\n   job with `mcp__anvil_media__media_job_cancel`" in text
    assert "Except for the mandatory fail-closed cancellation in step 7" in text
    assert (
        "`mcp__anvil_media__media_job_cancel` only when the user asks to cancel that\n"
        "    job"
    ) in text


def test_hermes_skill_documents_exact_resume_submission_shape():
    text = SKILL.read_text(encoding="utf-8")
    assert "Pass exactly its five submission fields" in text
    for field in (
        "`workflow_id`",
        "`version`",
        "`parameters`",
        "`quality_profile`",
        "`idempotency_key`",
    ):
        assert field in text
    assert "Do not pass `job_id` or\n   `approval_transaction_id` to that tool" in text
    assert "use `job_id` only for the same-job\n   equality check" in text
    assert "use `approval_transaction_id` only to correlate the\n   reported operator approval" in text


def test_hermes_shaped_mcp_flow_reaches_worker_and_retrieves_owned_artifact(tmp_path):
    tasks = service(tmp_path)
    gateway = ProtocolGateway(
        caller=CALLER,
        tasks=tasks,
        registry=tasks.operations.registry,
        artifacts=tasks.operations.artifacts,
        public_origin="http://127.0.0.1:8080",
    )

    submitted = gateway.mcp_request(
        _rpc(
            "media_workflow_run",
            {
                "workflow_id": "image.test",
                "version": "v1",
                "parameters": {"prompt": "mountain"},
                "idempotency_key": "hermes-media-smoke",
            },
            1,
        )
    )
    job = _structured(submitted)["job"]
    assert job["state"] == "queued"
    assert tasks.backend.deleted == []

    stored = tasks.operations.jobs.get(job["id"], principal="hermes")
    artifact = tasks.operations.artifacts.ingest(
        stored,
        io.BytesIO(b"\x89PNG\r\n\x1a\nhermes-smoke"),
        media_type="image/png",
        max_bytes=1024,
        retention_seconds=60,
    )
    tasks.operations.jobs.add_artifact(artifact)
    tasks.operations.jobs.transition(job["id"], JobState.RUNNING, principal="hermes")
    tasks.operations.jobs.transition(job["id"], JobState.COMPLETED, principal="hermes")

    status = _structured(
        gateway.mcp_request(_rpc("media_job_status", {"job_id": job["id"]}, 2))
    )["job"]
    assert status["state"] == "completed"
    assert status["artifacts"][0]["id"] == artifact.id

    inspected = _structured(
        inspection_response := gateway.mcp_request(
            _rpc("media_artifact_inspect", {"artifact_id": artifact.id}, 3)
        )
    )["artifact"]
    image_content = inspection_response["result"]["content"][1]
    payload = gateway.artifact(artifact.id)
    assert inspected["resource"] == f"/artifacts/{artifact.id}"
    assert inspected["sha256"] == artifact.sha256
    assert image_content["type"] == "image"
    assert image_content["mimeType"] == "image/png"
    assert payload.data == b"\x89PNG\r\n\x1a\nhermes-smoke"


def test_unavailable_workflow_is_truthful_and_never_submitted(tmp_path):
    tasks = service(tmp_path)
    tasks.operations.registry.descriptor = replace(
        tasks.operations.registry.descriptor,
        available=False,
        unavailable_reasons=("quality_unreviewed",),
    )
    gateway = ProtocolGateway(
        caller=CALLER,
        tasks=tasks,
        registry=tasks.operations.registry,
        artifacts=tasks.operations.artifacts,
        public_origin="http://127.0.0.1:8080",
    )
    response = gateway.mcp_request(
        _rpc(
            "media_workflow_run",
            {
                "workflow_id": "image.test",
                "version": "v1",
                "parameters": {"prompt": "mountain"},
                "idempotency_key": "hermes-unavailable-smoke",
            },
            4,
        )
    )
    assert response["result"]["isError"] is True
    error = response["result"]["structuredContent"]["error"]
    assert error["code"] == "media_admission_rejected"
    assert error["details"]["state"] == "unavailable"
    assert tasks.operations.jobs.nonterminal() == []
