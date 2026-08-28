from __future__ import annotations

import threading
import time

from anvil_serving.a2a.http import handle_jsonrpc
from anvil_serving.a2a.tasks import A2AMediaTasks
from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.comfyui import ComfyUIClient, WorkflowCompatibility
from anvil_serving.media.contracts import JobState, ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.errors import MediaError
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.workflows import canonical_digest


CALLER = {
    "principal": "hermes",
    "scopes": ["media:read", "media:submit", "media:cancel"],
}


def _workflow():
    graph = {"1": {"class_type": "Test", "inputs": {"prompt": ""}}}
    return WorkflowDescriptor(
        id="image.test", version="v1", kind="image", service_target="media-worker",
        graph_digest=canonical_digest(graph), parameters={"prompt": ParameterSpec("string", max_length=100)},
        bindings=(ParameterBinding("prompt", "1", "prompt"),), output_nodes=("1",),
        output_mime_types=("image/png",), available=True, unavailable_reasons=(),
    )


class Registry:
    descriptor = _workflow()

    def list(self):
        return [self.descriptor.as_public_dict()]

    def get(self, workflow_id, version):
        assert (workflow_id, version) == ("image.test", "v1")
        return self.descriptor

    def render(self, workflow_id, version, parameters):
        descriptor = self.get(workflow_id, version)
        values = descriptor.validate_parameters(parameters)
        return RenderedWorkflow(
            descriptor,
            {"1": {"class_type": "Test", "inputs": {"prompt": values["prompt"]}}},
            canonical_digest(values),
        )


class Backend(ComfyUIClient):
    def __init__(self):
        super().__init__("http://127.0.0.1:8188")
        self.deleted = []

    def compatibility(self, workflow):
        return WorkflowCompatibility(workflow.id, workflow.version, True, True)

    def submit(self, workflow, *, job_id):
        return "prompt-one"

    def delete_queued_prompt(self, prompt_id):
        self.deleted.append(prompt_id)


class ColdBackend(Backend):
    def compatibility(self, workflow):
        raise MediaError("backend_unavailable", "media worker is cold", status=503)


def service(tmp_path):
    operations = MediaOperations(
        Registry(), MediaJobStore(tmp_path / "jobs.sqlite3"), ArtifactStore(tmp_path / "artifacts")
    )
    return A2AMediaTasks(operations, Backend())


def cold_service(tmp_path):
    operations = MediaOperations(
        Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
        lifecycle_preview=lambda _job, _principal, service: {
            "transactionId": "preview-transaction",
            "service": service,
            "action": "prepare",
            "humanRequired": True,
            "manifest": "serves.comfyui.toml",
        },
    )
    return A2AMediaTasks(operations, ColdBackend())


def send_request(request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "SendMessage",
        "params": {
            "message": {
                "role": "ROLE_USER",
                "messageId": "message-one",
                "parts": [
                    {
                        "data": {
                            "workflowId": "image.test",
                            "version": "v1",
                            "parameters": {"prompt": "mountain"},
                            "idempotencyKey": "request-one",
                        }
                    }
                ],
            },
            "configuration": {"returnImmediately": True},
        },
    }


def test_send_poll_and_cancel_project_one_shared_job(tmp_path):
    tasks = service(tmp_path)
    sent = handle_jsonrpc(send_request(), tasks=tasks, caller=CALLER)
    task = sent["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_SUBMITTED"
    assert task["status"]["timestamp"].endswith("Z")
    assert task["metadata"]["workflow"] == {"id": "image.test", "version": "v1"}
    polled = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task["id"]}},
        tasks=tasks, caller=CALLER,
    )
    assert polled["result"] == task
    canceled = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "CancelTask", "params": {"id": task["id"]}},
        tasks=tasks, caller=CALLER,
    )
    assert canceled["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert tasks.backend.deleted == ["prompt-one"]


def test_a2a_auth_and_cross_principal_match_domain_isolation(tmp_path):
    tasks = service(tmp_path)
    task_id = handle_jsonrpc(send_request(), tasks=tasks, caller=CALLER)["result"]["task"]["id"]
    denied = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}},
        tasks=tasks, caller={"principal": "another", "scopes": ["media:read"]},
    )
    assert denied["error"]["code"] == -32001
    assert denied["error"]["message"] == "Task not found"
    assert denied["error"]["data"][0]["reason"] == "TASK_NOT_FOUND"
    no_scope = handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 3, "method": "CancelTask", "params": {"id": task_id}},
        tasks=tasks, caller={"principal": "hermes", "scopes": ["media:read"]},
    )
    assert no_scope["error"]["message"] == "scope_denied"


def test_raw_graph_or_file_part_is_rejected_before_backend(tmp_path):
    tasks = service(tmp_path)
    request = send_request()
    request["params"]["message"]["parts"][0]["raw"] = "unsafe"
    response = handle_jsonrpc(request, tasks=tasks, caller=CALLER)
    assert response["error"]["message"] == "invalid_a2a_message"
    assert tasks.operations.jobs.nonterminal() == []


def test_send_message_requires_message_id_and_canonical_error_details(tmp_path):
    tasks = service(tmp_path)
    request = send_request()
    del request["params"]["message"]["messageId"]
    response = handle_jsonrpc(request, tasks=tasks, caller=CALLER)
    assert response["error"]["code"] == -32602
    assert response["error"]["data"][0]["@type"] == (
        "type.googleapis.com/google.rpc.BadRequest"
    )
    assert tasks.operations.jobs.nonterminal() == []


def test_missing_task_and_terminal_cancel_use_a2a_1_0_errors(tmp_path):
    tasks = service(tmp_path)
    missing = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "GetTask",
            "params": {"id": "job_missing-task-id"},
        },
        tasks=tasks,
        caller=CALLER,
    )
    assert missing["error"]["code"] == -32001
    assert isinstance(missing["error"]["data"], list)

    task_id = handle_jsonrpc(send_request(2), tasks=tasks, caller=CALLER)["result"][
        "task"
    ]["id"]
    handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "CancelTask",
            "params": {"id": task_id},
        },
        tasks=tasks,
        caller=CALLER,
    )
    repeated = handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "CancelTask",
            "params": {"id": task_id},
        },
        tasks=tasks,
        caller=CALLER,
    )
    assert repeated["error"]["code"] == -32002
    assert repeated["error"]["data"][0]["reason"] == "TASK_NOT_CANCELABLE"


def test_send_message_blocks_by_default_until_terminal(tmp_path):
    tasks = service(tmp_path)
    request = send_request()
    request["params"].pop("configuration")
    responses = []

    thread = threading.Thread(
        target=lambda: responses.append(
            handle_jsonrpc(request, tasks=tasks, caller=CALLER)
        )
    )
    thread.start()
    deadline = time.monotonic() + 5
    jobs = []
    while time.monotonic() < deadline:
        jobs = tasks.operations.jobs.nonterminal()
        if jobs and jobs[0].state == JobState.QUEUED:
            break
        time.sleep(0.01)
    assert len(jobs) == 1
    assert jobs[0].state == JobState.QUEUED
    tasks.operations.jobs.transition(
        jobs[0].id,
        JobState.RUNNING,
        principal="hermes",
    )
    tasks.operations.jobs.transition(
        jobs[0].id,
        JobState.COMPLETED,
        principal="hermes",
    )
    thread.join(5)
    assert not thread.is_alive()
    assert responses[0]["result"]["task"]["status"]["state"] == (
        "TASK_STATE_COMPLETED"
    )


def test_send_message_blocks_only_until_input_is_required(tmp_path):
    tasks = cold_service(tmp_path)
    request = send_request()
    request["params"].pop("configuration")

    response = handle_jsonrpc(request, tasks=tasks, caller=CALLER)

    task = response["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert task["status"]["message"]["parts"] == [
        {"text": "Media worker lifecycle approval is required."}
    ]
