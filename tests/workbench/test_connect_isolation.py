"""Signed Connect peers sharing role grants must retain private ownership."""

from dataclasses import replace
import io
import time
from types import SimpleNamespace

import pytest

from anvil_serving.workbench_app.pi_sessions import PiConversationService, PiSessionStore, PiTaskBinding
from tests.workbench.test_service import site as site


def sign_in(call, subject, role="admin"):
    status, result = call("GET", "session", namespace="observatory", subject=subject, role=role)
    assert status == 200
    return result["data"]["identity"]


def test_shared_role_conversations_preferences_and_old_records_stay_private(site):
    console, call, mode = site
    if mode != "connect":
        pytest.skip("Connect role boundary")
    service = console.workbench
    owner = sign_in(call, "alice")
    conversation = {"id": "private-chat", "connector_id": "selected", "model": "model-a",
                    "status": "completed", "messages": [{"role": "user", "content": "private"}]}
    service.store.put("conversation", owner, "private-chat", conversation)
    service.store.put("conversation", "alice", "old-shared-chat", conversation | {"id": "old-shared-chat"})
    assert call("POST", "preferences", {"density": "compact"})[0] == 200
    assert call("GET", "conversations/private-chat")[1]["data"] == conversation

    peer = sign_in(call, "bob")
    assert peer != owner
    assert call("GET", "catalog", subject="bob")[1]["data"]["connectors"][0]["permitted"]
    assert call("GET", "conversations", subject="bob")[1]["data"]["items"] == []
    assert call("GET", "preferences", subject="bob")[1]["data"] == {}
    for key in ("private-chat", "old-shared-chat"):
        assert call("GET", f"conversations/{key}", subject="bob")[0] == 404
        assert call("POST", f"conversations/{key}/cancel", {}, subject="bob")[0] == 404
        assert call("POST", f"conversations/{key}/delete", {}, subject="bob")[0] == 404
    assert call("POST", "preferences", {"density": "comfortable"}, subject="bob")[0] == 200

    assert sign_in(call, "alice") == owner
    assert call("GET", "preferences")[1]["data"] == {"density": "compact"}
    assert call("GET", "conversations/private-chat")[0] == 200
    assert call("GET", "conversations/old-shared-chat")[0] == 404
    # No automatic migration, deletion, or adoption of ambiguous old ownership.
    assert service.store.get("conversation", "alice", "old-shared-chat")["id"] == "old-shared-chat"
    assert call("POST", "conversations/private-chat/delete", {})[0] == 200


def test_shared_role_chat_retries_and_continuations_are_subject_scoped(site, monkeypatch):
    console, call, mode = site
    if mode != "connect":
        pytest.skip("Connect role boundary")
    playground = console.workbench.playground
    playground.config["presets"] = [{"id": "brief", "temperature": 0, "max_tokens": 32}]
    requests = []

    def respond(request, **_kwargs):
        requests.append(request)
        return io.BytesIO(b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n')

    monkeypatch.setattr(playground, "open_request", respond)
    body = {"connector_id": "selected", "model": "model-a", "preset_id": "brief",
            "message": "private prompt", "request_id": "same-request"}
    conversations = {}
    for subject in ("alice", "bob"):
        sign_in(call, subject)
        status, result = call("POST", "messages", body, subject=subject)
        assert status == 200
        conversations[subject] = result["data"]["id"]
        deadline = time.monotonic() + 2
        while call("GET", "conversations/" + conversations[subject], subject=subject)[1]["data"]["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert call("POST", "messages", body, subject=subject)[1]["data"]["id"] == conversations[subject]
    assert conversations["alice"] != conversations["bob"] and len(requests) == 2
    assert call("POST", "messages", body | {"request_id": "continue", "conversation_id": conversations["alice"]}, subject="bob")[0] == 404
    assert len(requests) == 2
    sign_in(call, "alice")
    assert call("POST", "messages", body)[1]["data"]["id"] == conversations["alice"]
    assert len(requests) == 2


def test_shared_role_cannot_read_or_control_another_subjects_pi_task(site, tmp_path, monkeypatch):
    console, call, mode = site
    if mode != "connect":
        pytest.skip("Connect role boundary")
    service = console.workbench
    owner = sign_in(call, "alice")
    service.config["projects"] = [{"id": "product", "resource_id": "serve-a"}]
    # Grant execution equally, so denial must come from private ownership.
    console.access._connect_roles["admin"] = replace(
        console.access._connect_roles["admin"], actions=frozenset({"project.execute", "playground.request"}))
    binding = PiTaskBinding(principal_id=owner, project_id="product", task_id="plan:T001",
                            lease_id="lease-a", runner_id="runner-a", provider_id="provider-a")
    service.pi = SimpleNamespace(close=lambda: None, _session_json=PiConversationService._session_json)
    service.pi_store = PiSessionStore(tmp_path / "pi", runtime_root=tmp_path / "runtime")
    item, _ = service.pi_store.create(binding, "start-a", model_id="model-a", thinking_level="off")
    service.store.put("task-binding", owner, "start-a", {"id": "start-a", "project_id": "product",
        "task_id": "plan:T001", "lease_id": "lease-a", "status": "ready"})
    service.store.put("pi-command", owner, "command-a", {"session_id": item.session_id, "result": {"accepted": True}})
    monkeypatch.setattr(service.projects, "pi_binding", lambda row: binding)
    monkeypatch.setattr(service.projects, "binding_for_pi", lambda row: {"id": "start-a"})
    suffix = "?project=product&task=plan%3AT001"
    assert call("GET", f"pi/sessions/{item.session_id}" + suffix)[0] == 200
    assert len(call("GET", "pi/sessions" + suffix)[1]["data"]["items"]) == 1

    sign_in(call, "bob")
    assert call("GET", "pi/sessions" + suffix, subject="bob")[1]["data"]["items"] == []
    for route in ("pi/starts/start-a", f"pi/sessions/{item.session_id}",
                  f"pi/sessions/{item.session_id}/events", f"pi/sessions/{item.session_id}/commands/command-a"):
        assert call("GET", route + suffix, subject="bob")[0] == 404
    for action in ("stop", "delete", "resume", "command"):
        body = {"project_id": "product", "task_id": "plan:T001"}
        if action == "command":
            body.update(request_id="command-a", name="abort", payload={})
        assert call("POST", f"pi/sessions/{item.session_id}/{action}", body, subject="bob")[0] == 404

    assert sign_in(call, "alice") == owner
    assert call("GET", "pi/starts/start-a" + suffix)[1]["data"]["sessions"][0]["session_id"] == item.session_id
    assert call("GET", f"pi/sessions/{item.session_id}/commands/command-a" + suffix)[1]["data"] == {"accepted": True}
