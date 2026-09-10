from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.workbench_app.pi_rpc import PiRpcClient
from anvil_serving.workbench_app.pi_sessions import (
    PiConversationService, PiSessionAccessError, PiSessionError, PiSessionStore, PiTaskBinding,
)


class _Coordinator:
    denied = False

    def validate_pi_binding(self, _binding):
        if self.denied:
            raise PiSessionAccessError("lease expired")


def _binding(**changes):
    return PiTaskBinding(**({"principal_id": "operator-a", "project_id": "project-a", "task_id": "task-4", "lease_id": "lease-1", "runner_id": "runner-1", "provider_id": "provider-a"} | changes))


class Process:
    def __init__(self, session):
        self.session, self.stdin, self.stdout = session, self, object()
        self.chunks, self.writes = [], []
        self.closed = False
        self.fail = None
        self.state_override = {}

    def poll(self):
        return None

    def terminate(self):
        self.closed = True

    def flush(self):
        pass

    def write(self, raw):
        command = json.loads(raw)
        self.writes.append(command)
        if command["type"] != self.fail:
            if command["type"] == "set_model":
                self.session = replace(self.session, model_id=command["modelId"])
            if command["type"] == "set_thinking_level":
                self.session = replace(self.session, thinking_level=command["level"])
        state = {"sessionId": self.session.session_id, "sessionFile": "/sessions/live.jsonl", "model": {"id": self.session.model_id, "provider": self.session.binding.provider_id}, "thinkingLevel": self.session.thinking_level} | self.state_override
        result = {"type": "response", "id": command["id"], "command": command["type"], "success": command["type"] != self.fail, "data": state if command["type"] == "get_state" else None}
        self.chunks.append((json.dumps(result) + "\n").encode())
        return len(raw)


def service(tmp_path, **options):
    store = PiSessionStore(tmp_path)
    coordinator = _Coordinator()
    processes, stopped = [], []

    def factory(session, _session_dir):
        process = Process(session)
        processes.append(process)
        return PiRpcClient(("pi",), cwd=tmp_path, environment={}, process_factory=lambda *_a, **_k: process, read_chunk=lambda p, _size: p.chunks.pop(0) if p.chunks else b"")

    svc = PiConversationService(store, coordinator, factory, allowed_models={"provider-a": frozenset({"model-a", "model-b"}), "provider-b": frozenset({"model-c"})}, allowed_thinking=frozenset({"low", "high"}), stop_runner=lambda s: stopped.append(s.session_id), **options)
    return svc, store, processes, stopped, coordinator


def start(svc, key="start-1", binding=None, **kwargs):
    return svc.new(binding or _binding(), key, model_id="model-a", thinking_level="low", **kwargs)


def test_native_path_tracks_live_file_and_rejects_traversal_and_symlinks(tmp_path):
    store = PiSessionStore(tmp_path)
    session, _ = store.create(_binding(), "start")
    for value in ("/sessions/../outside.jsonl", "/sessions/nested/file.jsonl", "/tmp/file.jsonl", str(tmp_path / "x.jsonl")):
        with pytest.raises(PiSessionError):
            store.record_official_state(session.session_id, {"sessionId": session.session_id, "sessionFile": value})
    file = store.session_dir(session.session_id) / "live.jsonl"
    state = {"sessionId": session.session_id, "sessionFile": "/sessions/live.jsonl"}
    saved = store.record_official_state(session.session_id, state)
    assert saved.official_session_file == str(file)  # Native Pi has not flushed yet.
    file.write_text("first\n")
    assert store.record_official_state(session.session_id, state).resume_file_name == "live.jsonl"
    with file.open("a") as output:
        output.write("second\n")
    assert Path(store.get(session.session_id).official_session_file).read_text() == "first\nsecond\n"
    file.unlink()
    file.symlink_to(tmp_path / "outside")
    with pytest.raises(PiSessionError):
        store.record_official_state(session.session_id, state)


def test_binding_idempotency_and_capacity_before_reservation(tmp_path):
    svc, store, processes, _, _ = service(tmp_path)
    first = start(svc)
    assert first["status"] == "running"
    assert start(svc)["session_id"] == first["session_id"]
    assert len(processes) == 1
    with pytest.raises(PiSessionAccessError):
        store.resume(first["session_id"], _binding(principal_id="other"))
    with pytest.raises(PiSessionError, match="limit"):
        start(svc, "capacity-rejected")
    assert store.find_start(_binding(), "capacity-rejected") is None
    assert len(store.all()) == 1


def test_branch_uses_latest_live_history_and_new_native_identity(tmp_path):
    svc, store, _, _, _ = service(tmp_path)
    parent = start(svc)
    live = Path(store.get(parent["session_id"]).official_session_file)
    live.write_text("first turn\nsecond turn\n")
    svc.stop(parent["session_id"], _binding())
    branch = start(svc, "branch", parent_session_id=parent["session_id"])
    assert branch["official_session_id"] != parent["official_session_id"]
    assert (store.session_dir(branch["session_id"]) / "branch-source.jsonl").read_text() == live.read_text()


def test_successful_target_outcomes_persist_and_failures_do_not(tmp_path):
    svc, store, processes, _, _ = service(tmp_path)
    key = start(svc)["session_id"]
    with pytest.raises(PiSessionAccessError, match="provider"):
        svc.command(key, _binding(), "set_model", {"provider": "provider-b", "model_id": "model-c"})
    svc.command(key, _binding(), "set_model", {"provider": "provider-a", "model_id": "model-b"})
    assert store.get(key).model_id == "model-a"
    svc._collect(key)
    svc._collect(key)
    assert store.get(key).model_id == "model-b"
    svc._collect(key)
    processes[0].fail = "set_thinking_level"
    svc.command(key, _binding(), "set_thinking_level", {"level": "high"})
    svc._collect(key)
    assert store.get(key).thinking_level == "low"
    svc._collect(key)
    processes[0].fail = None
    svc.command(key, _binding(), "set_thinking_level", {"level": "high"})
    svc._collect(key)
    svc._collect(key)
    assert store.get(key).thinking_level == "high"


def test_accepted_conversation_commands_retain_only_display_metadata(tmp_path, monkeypatch):
    svc, store, _, _, _ = service(tmp_path)
    key = start(svc)["session_id"]

    svc.command(key, _binding(), "prompt", {"message": "Inspect the failing test"})
    prompt = store.events_after(key, 0).events[-1]
    assert prompt.kind == "command_accepted"
    assert prompt.data == {
        "name": "prompt",
        "command_id": prompt.data["command_id"],
        "message": "Inspect the failing test",
    }

    client = svc._clients[key]
    sent = []
    monkeypatch.setattr(client, "extension_response", lambda request_id, response: sent.append((request_id, response)))
    svc.command(key, _binding(), "extension_response", {"request_id": "request-1", "response": {"confirmed": True}})
    extension = store.events_after(key, prompt.cursor).events[-1]
    assert sent == [("request-1", {"confirmed": True})]
    assert extension.data == {"name": "extension_response", "command_id": None, "request_id": "request-1"}
    assert "response" not in extension.data

    before = store.get(key).next_cursor
    writes = len(client._process.writes)
    with pytest.raises(ValueError, match="retained conversation limit"):
        svc.command(key, _binding(), "prompt", {"message": "🧪" * 4_001})
    assert len(client._process.writes) == writes
    assert store.get(key).next_cursor == before

    monkeypatch.setattr(client, "prompt", lambda _message: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError, match="write failed"):
        svc.command(key, _binding(), "prompt", {"message": "must not be accepted"})
    assert store.get(key).next_cursor == before


def test_failed_state_stops_runner_and_records_failure_without_false_running(tmp_path):
    svc, store, processes, stopped, _ = service(tmp_path)
    key = start(svc)["session_id"]
    processes[0].state_override = {"sessionId": "wrong-parent"}
    svc.command(key, _binding(), "get_state", {})
    svc._collect(key)
    assert stopped == [key]
    assert store.get(key).status == "recoverable"
    assert any(e.data.get("ok") is False for e in store.events_after(key, 0).events)


def test_restart_retained_runners_count_and_expire_without_browser(tmp_path):
    svc, store, _, _, _ = service(tmp_path)
    key = start(svc)["session_id"]
    stopped = []
    recovered = PiConversationService(store, _Coordinator(), svc._runner_factory, allowed_models=svc._models, allowed_thinking=svc._thinking, reconcile=lambda *_: "running", stop_runner=lambda s: stopped.append(s.session_id), now=lambda: store.get(key).created_at + 15000)
    assert key in recovered._active and not recovered._clients
    with pytest.raises(PiSessionError, match="limit"):
        start(recovered, "restart-bypass")
    assert recovered.sweep() == (key,)
    assert stopped == [key]


def test_close_stops_retained_identity_and_delete_releases_retention(tmp_path):
    svc, store, processes, stopped, _ = service(tmp_path, reconcile=lambda *_: "absent")
    key = start(svc)["session_id"]
    svc.close()
    assert stopped == [key] and processes[0].closed
    assert svc.delete(key, _binding())["deleted"]
    assert not store.all()
    assert start(svc)["session_id"] != key


def test_events_are_bounded_and_report_gap(tmp_path):
    store = PiSessionStore(tmp_path, max_events=2)
    session, _ = store.create(_binding(), "start")
    for number in range(3):
        store.append(session.session_id, "text", {"n": number})
    page = store.events_after(session.session_id, 0)
    assert page.gap and [e.data["n"] for e in page.events] == [1, 2]


@pytest.mark.parametrize("fault", ["protocol", "event_overflow", "journal"])
def test_terminal_source_fault_stops_owned_runner_and_releases_capacity(tmp_path, monkeypatch, fault):
    svc, store, processes, stopped, _ = service(tmp_path)
    session = start(svc); key = session["session_id"]
    if fault == "protocol":
        processes[0].chunks.append(b"not-json\n")
    elif fault == "event_overflow":
        processes[0].chunks.append((json.dumps({"type":"message_update", "data":"x"*40000})+"\n").encode())
    else:
        monkeypatch.setattr(store, "append", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
        processes[0].chunks.append(b'{"type":"agent_start"}\n')
    svc._collect(key)
    assert stopped == [key]
    assert key not in svc._active and key not in svc._clients
    assert store.get(key).status == "recoverable"


@pytest.mark.parametrize("observed", ["old", "requested"])
def test_target_intent_is_durable_before_dispatch_and_restart_observes_without_replay(tmp_path, observed):
    svc, store, processes, stopped, coordinator = service(tmp_path)
    key = start(svc)["session_id"]
    original = processes[0].write
    def write(raw):
        if json.loads(raw)["type"] == "set_model":
            pending = store.get(key).pending_target
            assert pending["old"]["model_id"] == "model-a"
            assert pending["requested"]["model_id"] == "model-b"
        return original(raw)
    processes[0].write = write
    svc.command(key, _binding(), "set_model", {"provider":"provider-a", "model_id":"model-b"})
    assert store.get(key).pending_target
    restored_processes = []
    def attach(session, _directory):
        native = replace(session, model_id="model-a" if observed == "old" else "model-b")
        process = Process(native); restored_processes.append(process)
        return PiRpcClient(("pi",), cwd=tmp_path, environment={}, process_factory=lambda *_a, **_k: process, read_chunk=lambda p,_n:p.chunks.pop(0) if p.chunks else b"")
    restarted = PiConversationService(store, coordinator, attach, allowed_models={"provider-a":frozenset({"model-a","model-b"})}, allowed_thinking=frozenset({"low","high"}), reconcile=lambda *_a:"running", attach_factory=attach)
    result = restarted.resume(key, _binding())
    assert result["status"] == "running"
    assert store.get(key).pending_target is None
    assert result["model_id"] == ("model-a" if observed == "old" else "model-b")
    assert [x["type"] for x in restored_processes[0].writes] == ["get_state"]


def test_start_key_binds_initial_target_and_parent(tmp_path):
    svc, store, processes, stopped, _ = service(tmp_path)
    key = start(svc)["session_id"]
    for changes in ({"model_id":"model-b"}, {"thinking_level":"high"}, {"parent_session_id":key}):
        with pytest.raises(PiSessionAccessError):
            svc.new(_binding(), "start-1", **({"model_id":"model-a", "thinking_level":"low"} | changes))
