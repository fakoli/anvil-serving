import threading
import time
from types import SimpleNamespace


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
