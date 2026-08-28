from __future__ import annotations

from anvil_serving.a2a.http import handle_jsonrpc
from anvil_serving.a2a.tasks import A2AMediaTasks
from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.comfyui import ComfyUIClient, WorkflowCompatibility
from anvil_serving.media.contracts import ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor
from anvil_serving.media.jobs import MediaJobStore
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


def service(tmp_path):
    operations = MediaOperations(
        Registry(), MediaJobStore(tmp_path / "jobs.sqlite3"), ArtifactStore(tmp_path / "artifacts")
    )
    return A2AMediaTasks(operations, Backend())


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
    assert denied["error"]["message"] == "job_not_found"
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
