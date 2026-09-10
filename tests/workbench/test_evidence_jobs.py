import threading
import time
from types import SimpleNamespace

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.evidence_jobs import EvidenceJobs
from anvil_serving.workbench_app.store import PrivateStore


class Projects:
    def __init__(self):
        self.entered = threading.Event()
        self.finish = threading.Event()
        self.calls = []

    def binding(self, session, key, execute=False):
        if session.principal.identity != "alice":
            raise ObservatoryError("not_found", "Unavailable", 404)
        return {"id": key}

    def review_evidence(self, session, key):
        self.calls.append(key)
        self.entered.set()
        assert self.finish.wait(5)

    verify_evidence = submit_evidence = release = review_evidence


def test_bounded_background_work_does_not_block_http_or_allow_conflicting_pi(tmp_path):
    projects = Projects()
    store = PrivateStore(tmp_path / "private.sqlite")
    jobs = EvidenceJobs(projects, store, threading.RLock())
    alice = SimpleNamespace(principal=SimpleNamespace(identity="alice"))
    try:
        response = jobs.start(alice, "binding-a", "review")
        assert response["accepted"] and projects.entered.wait(1)
        assert jobs.latest(alice, "binding-a")["status"] == "running"
        with pytest.raises(ObservatoryError, match="Wait for"):
            jobs.ensure_idle("binding-a")
        with pytest.raises(ObservatoryError, match="Wait for"):
            jobs.start(alice, "binding-a", "release")
        jobs.start(alice, "binding-b", "review")
        with pytest.raises(ObservatoryError, match="Both evidence workers"):
            jobs.start(alice, "binding-c", "review")
        with pytest.raises(ObservatoryError, match="Unavailable"):
            jobs.latest(SimpleNamespace(principal=SimpleNamespace(identity="bob")), "binding-a")
    finally:
        projects.finish.set()
        jobs.close()
    assert jobs.latest(alice, "binding-a")["status"] == "complete"
    assert sorted(projects.calls) == ["binding-a", "binding-b"]
    store.close()


def test_restart_preserves_uncertain_submit_without_replaying(tmp_path):
    store = PrivateStore(tmp_path / "private.sqlite")
    store.put("artifact-job", "alice", "old", {"id": "old", "binding_id": "binding-a", "action": "submit", "status": "running"})
    projects = Projects()
    jobs = EvidenceJobs(projects, store, threading.RLock())
    alice = SimpleNamespace(principal=SimpleNamespace(identity="alice"))
    try:
        assert jobs.latest(alice, "binding-a")["status"] == "interrupted"
        with pytest.raises(ObservatoryError, match="Reconcile"):
            jobs.start(alice, "binding-a", "submit", "a" * 64)
        assert not projects.calls
    finally:
        jobs.close()
        store.close()


def test_terminal_persistence_failure_releases_capacity_and_retains_uncertainty(tmp_path, monkeypatch):
    store = PrivateStore(tmp_path / "private.sqlite")
    projects = Projects()
    projects.finish.set()
    jobs = EvidenceJobs(projects, store, threading.RLock())
    alice = SimpleNamespace(principal=SimpleNamespace(identity="alice"))
    original_put = store.put

    def fail_terminal(kind, owner, key, body):
        if kind == "artifact-job" and body.get("status") == "complete":
            raise OSError("terminal persistence unavailable")
        return original_put(kind, owner, key, body)

    monkeypatch.setattr(store, "put", fail_terminal)
    try:
        response = jobs.start(alice, "binding-a", "review")
        deadline = time.monotonic() + 2
        while jobs.busy("binding-a") and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not jobs.busy("binding-a")
        assert store.get("artifact-job", "alice", response["job"]["id"])["status"] == "running"
        assert projects.calls == ["binding-a"]
    finally:
        jobs.close()

    monkeypatch.setattr(store, "put", original_put)
    recovered = EvidenceJobs(projects, store, threading.RLock())
    try:
        assert recovered.latest(alice, "binding-a")["status"] == "interrupted"
        assert projects.calls == ["binding-a"]
    finally:
        recovered.close()
        store.close()
