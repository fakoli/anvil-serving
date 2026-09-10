import io
import json
import os
import threading
import time

import pytest

from anvil_serving.observability.dashboard.access import Access, Principal, Session
from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.playground import Playground
from anvil_serving.workbench_app.store import PrivateStore


def identity(name="alice", resources=("serve-a",), actions=("playground.request",)):
    return Session(name, "csrf", Principal(name, name, "operator", frozenset(resources), frozenset(actions)), time.time() + 60)


@pytest.fixture
def chat(tmp_path):
    config = {"connectors": [{"id": "local", "label": "Local", "resource_id": "serve-a", "base_url": "http://127.0.0.1:30000/v1", "models": ["selected"], "max_output_tokens": 64, "token_env": "MODEL_TOKEN"}],
              "presets": [{"id": "precise", "label": "Precise", "temperature": 0, "max_tokens": 100, "system": "Answer briefly."}]}
    access = Access([], authenticate=lambda *_: False, origin="https://console.example.test", base_path="/", operate=True)
    store = PrivateStore(tmp_path / "private.sqlite")
    calls = []
    gate = threading.Event()

    def respond(request, **kwargs):
        calls.append(request)
        gate.wait(2)
        return io.BytesIO(b'data: {"choices":[{"delta":{"content":"<script>literal</script>"}}]}\n\n'
                          b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":3,"total_tokens":12}}\n\n'
                          b'data: [DONE]\n\n')

    playground = Playground(config, store, access, {"MODEL_TOKEN": "private-test-value"}, open_request=respond)
    yield playground, calls, gate, store
    gate.set()
    playground.close()
    store.close()


def message(**changes):
    return {"connector_id": "local", "model": "selected", "preset_id": "precise", "message": "Test", "request_id": "request-one", **changes}


def wait_terminal(playground, key, user=None):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = playground.read(user or identity(), key)
        if result["status"] not in {"running", "cancel_requested"}:
            return result
        time.sleep(0.005)
    raise AssertionError("request did not complete")


def test_exact_target_stream_usage_and_idempotent_request(chat):
    playground, calls, gate, _ = chat
    first = playground.send(identity(), message())
    repeated = playground.send(identity(), message())
    assert first["id"] == repeated["id"]
    gate.set()
    result = wait_terminal(playground, first["id"])
    assert len(calls) == 1
    payload = json.loads(calls[0].data)
    assert calls[0].full_url == "http://127.0.0.1:30000/v1/chat/completions"
    assert payload["model"] == "selected" and payload["max_tokens"] == 64
    assert result["status"] == "completed"
    assert result["messages"][-1]["content"] == "<script>literal</script>"
    assert result["usage"]["total_tokens"] == 12
    assert "private-test-value" not in json.dumps(result)
    assert "private-test-value" not in json.dumps(playground.catalog(identity()))


def test_scope_and_mutation_grants_and_private_history(chat):
    playground, calls, gate, _ = chat
    with pytest.raises(ObservatoryError) as error:
        playground.send(identity(actions=()), message())
    assert error.value.status == 403
    first = playground.send(identity(), message())
    with pytest.raises(ObservatoryError) as error:
        playground.read(identity("bob"), first["id"])
    assert error.value.status == 404
    assert playground.conversations(identity("bob"))["items"] == []
    with pytest.raises(ObservatoryError) as error:
        playground.read(identity(resources=()), first["id"])
    assert error.value.status == 403
    gate.set()


def test_unknown_model_and_conflicting_replay_never_dispatch(chat):
    playground, calls, gate, _ = chat
    with pytest.raises(ObservatoryError):
        playground.send(identity(), message(model="substitute"))
    assert calls == []
    first = playground.send(identity(), message())
    with pytest.raises(ObservatoryError) as error:
        playground.send(identity(), message(message="different"))
    assert error.value.code == "request_conflict"
    assert first["status"] == "running"
    gate.set()


def test_second_turn_uses_wire_schema_and_old_retry_is_not_replayed(chat):
    playground, calls, gate, _ = chat
    first_body = message()
    first = playground.send(identity(), first_body)
    gate.set()
    wait_terminal(playground, first["id"])
    second = playground.send(identity(), message(request_id="request-two", conversation_id=first["id"], message="Continue"))
    wait_terminal(playground, second["id"])
    assert len(calls) == 2
    assert all(set(row) == {"role", "content"} for row in json.loads(calls[1].data)["messages"])
    assert playground.send(identity(), first_body)["id"] == first["id"]
    assert len(calls) == 2
    playground.delete(identity(), first["id"])
    with pytest.raises(ObservatoryError) as error:
        playground.send(identity(), first_body)
    assert error.value.code == "request_retained"
    assert len(calls) == 2


def test_cancel_is_retained_and_deletion_does_not_resurrect(chat):
    playground, _, gate, store = chat
    first = playground.send(identity(), message())
    assert playground.cancel(identity(), first["id"])["status"] == "cancel_requested"
    with pytest.raises(ObservatoryError):
        playground.delete(identity(), first["id"])
    gate.set()
    assert wait_terminal(playground, first["id"])["status"] == "cancelled"
    playground.delete(identity(), first["id"])
    assert playground.conversations(identity())["items"] == []
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_restart_marks_inflight_interrupted_without_replay(tmp_path):
    path = tmp_path / "private.sqlite"
    store = PrivateStore(path)
    store.put("conversation", "alice", "one", {"status": "running", "output": "partial"})
    store.close()
    store = PrivateStore(path)
    store.recover("conversation", {"running"})
    assert store.get("conversation", "alice", "one")["status"] == "interrupted"
    store.close()
